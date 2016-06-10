#!/usr/bin/env python
"""
Manage cloud instances where metadata is defined in hiera

Usage:
  aws.py start <name> [ --config=<path> ]
  aws.py stop <name> [ --config=<path> ]
  aws.py toggle <name> [ --config=<path> ]
  aws.py status [ <name> ] [ --config=<path> ]
  aws.py check <name> [ --config=<path> ]
  aws.py (-h | --help)

Arguments:
  <name>  Name, fqdn, or other unique identifier in hiera to identify metadata
  start   Start and bootstrap the instance
  stop    Stop and terminate (destroy) the instance
  toggle  Start if stopped, stop if started
  status  Print name, role, and public IP of instances with optional filter
  check   Print all found metadata

Options:
  -h --help        Display this help
  --config=<path>  Hiera config [default: /etc/puppet/hiera.yaml]

The base parameters required in hiera for a <name>:
  metadata:hostname  hostname
  metadata:domain    domain name
  metadata:role      puppet role to assign
  metadata:repo      git repo url to clone puppet config from

A metadata:provider field is also checked, if set to 'aws' also set:
  metadata:aws:subnet    appropriate subnet id for instance type
  metadata:aws:secgroup  security group (single) to apply
  metadata:aws:keypair   ssh keypair to install
  metadata:aws:ami       id for instance to boot
  metadata:aws:type      type/size of instance to boot
  metadata:aws:region    aws region to start in

"""

from __future__ import print_function, division  # Only tested on Python 2.7 or later

import sys
import time
import subprocess
import boto3
from docopt import docopt


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
        # logging.debug('metadata_get  {0:<10} {1}'.format(item, metadata[item]))

    # build fqdn from hieradata
    metadata['fqdn'] = '{0}.{1}'.format(metadata['hostname'], metadata['domain'])

    # get parameters unique to a particular provider or platform
    if metadata['provider'] == 'aws':
        params = ['subnet', 'secgroup', 'keypair', 'ami', 'type', 'region']
        for item in params:
            metadata[item] = hiera_get('metadata:aws:{0}'.format(item), 'fqdn={0}'.format(node))
            # logging.debug('metadata_get  {0:<10} {1}'.format(item, metadata[item]))

    return metadata


def metadata_print(metadata):
    """Print out the metadata for verification
    Arguments:
        metadata = dict of metadata parameters from hiera
    Returns:
        None"""

    print('{0:<10} {1}'.format('parameter', 'value'))
    for key in metadata:
        print('{0:<10} {1}'.format(key, metadata[key]))


def ec2_start(resource, metadata):
    """Start an AWS EC2 instance and configures with cloud-init and puppet
    Arguments:
        resource = already open ec2 boto3.resource
        metadata = dict of parameters required to launch instance
    Returns:
        None"""

    # do minimal provisioning of machine through cloud-init
    # this installs git and bootstraps puppet to provision the rest
    # requires recent ubuntu (14.04/16.04) or RHEL/CentOS 7
    userdata = """#cloud-config
package_update: true
hostname: {hostname}
fqdn: {fqdn}
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
      hostname=metadata['hostname'], fqdn=metadata['fqdn'],
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

    # not sure if we really need to sleep before tagging but
    # we wait until running anyway which takes much longer than 1 second
    time.sleep(1)
    for instance in instances:
        # first set tags
        instance.create_tags(
            Resources=[instance.id],
            Tags=[
                {
                    'Key': 'Role',
                    'Value': metadata['role']
                },
                {
                    'Key': 'Name',
                    'Value': metadata['fqdn']
                },
            ]
        )

        # ensure system is running before we print address to connect to
        instance.wait_until_running()
        # instance.load()
        ec2_status(resource, metadata)


def ec2_stop(resource, metadata):
    """Stop and terminate an AWS EC2 instance
    Arguments:
        resource = already open ec2 boto3.resource
        instance_id = id of instance to terminate
    Returns:
        None"""
    instances = resource.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']},
                 {'Name': 'tag:Name', 'Values': [metadata['fqdn']]}, ])

    for instance in instances:
        print("Terminating instance id {0}".format(instance.id))
        # resource.instances.filter(InstanceIds=[instance.id]).stop()
        resource.instances.filter(InstanceIds=[instance.id]).terminate()


def ec2_status(resource, metadata, return_count=False):
    """Get the status of running instances
    Arguments:
        resource     = already open ec2 boto3.resource
        metadata     = dict containing key fqdn with value to filter on
        return_count = boolean option to return count or not
    Returns:
        None or count = int of number of running instances"""

    instances = resource.instances.filter(
        Filters=[{'Name': 'tag:Name', 'Values': [metadata['fqdn']]},
                 {'Name': 'instance-state-name', 'Values': ['pending', 'running']}, ])

    # get a count of the instances and then either return count or print results
    count = sum(1 for _ in instances)
    if return_count:
        # return count for conditional consumption in other functions
        return count
    else:
        # print for human consumption
        if count == 0:
            print("No instances running")
        else:
            print(count, "instances running")
            print('{:20} {:15} {:22} {:18} {}'.format(
                'instance_id', 'state', 'instance_name', 'public_ip_address', 'instance_role'))
            for instance in instances:
                print('{:20} {:15} {:22} {:18} {}'.format(
                    instance.id, instance.state['Name'], instance.tags[0]['Value'],
                    instance.public_ip_address, instance.tags[1]['Value']))


def main(arguments):
    """This is the main body of the program
    Arguments:
        arguments = dict of docopt options
    Returns:
        None"""

    # set up logging
    # logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)

    # pull the setup data from hiera based on the node identifier given
    # hiera will return nil for unset variables that were queried, set some safe defaults
    metadata = metadata_get(arguments['<name>'])
    if 'region' not in metadata or metadata['region'] == 'nil':
        metadata['region'] = 'us-east-1'

    # handle arguments from docopt
    if arguments['check']:
        # check comes first since we don't need actually valid metadata to print what we found
        metadata_print(metadata)

    elif arguments['status']:
        # this status may (eventually) print all running instances from any provider
        # not entirely sure how to handle multiple regions/datacenters yet
        # status has only an optional filter, so if we get here without a name print all
        if arguments['<name>'] is None or metadata['hostname'] == 'nil':
            metadata['fqdn'] = '*'
        resource = boto3.resource('ec2', region_name=metadata['region'])
        ec2_status(resource, metadata)

    elif metadata['provider'] == 'aws':
        # make connection to ec2 and then perform actions
        resource = boto3.resource('ec2', region_name=metadata['region'])

        if arguments['start']:
            ec2_start(resource, metadata)
        elif arguments['stop']:
            ec2_stop(resource, metadata)
        elif arguments['toggle']:
            # we either start or stop to go to inverse of the current state
            count = ec2_status(resource, metadata, return_count=True)
            if count == 0:
                ec2_start(resource, metadata)
            else:
                ec2_stop(resource, metadata)

    elif metadata['provider'] == 'do':
        print("Digitalocean not yet supported")

    else:
        # not really sure
        print("Unsupported metadata:provider from hiera: {0}".format(metadata['provider']))
        sys.exit(1)


if __name__ == "__main__":
    main(docopt(__doc__))
