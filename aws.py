#!/usr/bin/env python
"""This is a script that controls a specific AWS instance's lifecycle
   It currently accepts no arguments.  Run once to start devbox.  Run again to terminate."""

from __future__ import print_function, division  # Only tested on Python 2.7 or later

import time
import subprocess
import boto3


def hiera_get(item, variable, config='/etc/puppet/hiera.yaml'):
    """Call external hiera binary to get a value
    (sudo gem install -n /usr/local/bin hiera)

    Arguments:
        item     = str of hiera data item to query for
        variable = str of puppet vars to emulate
        config   = str of path to config file (optional)
    Returns:
        output   = str of value from hiera"""

    return subprocess.check_output(['hiera', '-c', config, item, variable],
                                   universal_newlines=True).strip()


def metadata_get(node):
    """Retrieves the metadata from hiera
    Arguments:
        node = str of node (fqdn) to get metadata for
    Returns:
        metadata = dict of metadata parameters for machine"""

    metadata = dict()

    # get parameters common to all hosting providers or platforms
    params = ['hostname', 'domain', 'provider', 'role', 'repo']
    for item in params:
        metadata[item] = hiera_get('metadata:{0}'.format(item), 'fqdn={0}'.format(node))

    # get parameters unique to a particular provider or platform
    if metadata['provider'] == 'aws':
        params = ['subnet', 'secgroup', 'keypair', 'ami', 'type']
        for item in params:
            metadata[item] = hiera_get('metadata:aws:{0}'.format(item), 'fqdn={0}'.format(node))

    return metadata


def ec2_start(resource, metadata):
    """Start an AWS EC2 instance
    Arguments:
        resource = already open ec2 boto3.resource
        metadata = dict of parameters required to launch instance
    Returns:
        instances = boto3 collection of started instances"""

    # do minimal provisioning of machine through cloud-init
    # this installs git and bootstraps puppet to provision the rest
    # requires recent ubuntu (14.04/16.04) or RHEL/CentOS 7
    userdata = """#cloud-config
package_update: true
hostname: {hostname}
fqdn: {hostname}.{domain}
manage_etc_hosts: true
packages:
  - git
write_files:
  - path: /etc/facter/facts.d/hostgroup.txt
    content: hostgroup=aws
  - path: /etc/facter/facts.d/role.txt
    content: role={role}
runcmd:
  - git clone {repo} /etc/puppet
  - /etc/puppet/support_scripts/bootstrap-puppet.sh""".format(
      hostname=metadata['hostname'], domain=metadata['domain'],
      role=metadata['role'], repo=metadata['repo'])

    instances = resource.create_instances(
        ImageId=metadata['ami'],
        MinCount=1,
        MaxCount=1,
        InstanceType=metadata['type'],
        SubnetId=metadata['subnet'],
        SecurityGroupIds=[metadata['secgroup']],
        KeyName=metadata['keypair'],
        UserData=userdata,
        BlockDeviceMappings=[
            {
                'DeviceName': '/dev/sda1',  # root so far, sometimes /dev/xvdh ?
                'Ebs': {
                    'VolumeSize': 20,
                    'DeleteOnTermination': True,
                    'VolumeType': 'gp2'
                },
            },
        ]
    )

    # not sure if we really need to sleep before tagging but see this often
    # and we wait until running later which takes much longer than 1 second
    time.sleep(1)
    for instance in instances:
        instance.create_tags(
            Resources=[instance.id],
            Tags=[
                {
                    'Key': 'Role',
                    'Value': metadata['role']
                },
                {
                    'Key': 'Name',
                    'Value': '{0}.{1}'.format(metadata['hostname'], metadata['domain'])
                },
            ]
        )
    # instance.console_output()
    # instance.modify_attribute()
    return instances


def ec2_stop(resource, instance_id):
    """Stop and terminate an AWS EC2 instance
    Arguments:
        resource = already open ec2 boto3.resource
        instance_id = id of instance to terminate
    Returns:
        None"""

    print("Terminating instance id {0}".format(instance_id))
    resource.instances.filter(InstanceIds=[instance_id]).stop()
    resource.instances.filter(InstanceIds=[instance_id]).terminate()


def cloud_start(node, resource):
    """Start a cloud instance
    Arguments:
        node     = str of node (fqdn) with preset metadata to startup
        resource = boto3 resource open to ec2 (refactor this away someday)
    Returns:
        None"""

    # pull the setup data from hiera
    metadata = metadata_get(node)

    # launch at requested cloud provider and pass in metadata to build
    if metadata['provider'] == 'aws':
        new_instances = ec2_start(resource, metadata)
        for instance in new_instances:
            # public IP is only allocated when system is running
            instance.wait_until_running()
            instance.load()
            print("id: {0}\naddress: {1}".format(instance.id, instance.public_ip_address))


def main():
    """This is the main body of the program
    Arguments:
    Returns:
        None"""

    # open ec2 connection
    resource = boto3.resource('ec2')
    node = 'devbox.ewplc.tk'

    # get a count of the running instances, stop all if any running, start if not
    instances = resource.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    # supposedly this sum does not load the whole collection in memory
    count = sum(1 for _ in instances)
    if count == 0:
        print("No instances running, starting up")
        cloud_start(node, resource)
    else:
        print(count, "instances running, killing VM(s)")
        for instance in instances:
            ec2_stop(resource, instance.id)


# this is the start of the program
if __name__ == "__main__":
    main()
