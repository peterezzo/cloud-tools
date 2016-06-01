#!/usr/bin/env python
"""This is a script that controls a specific AWS instance's lifecycle
   It currently accepts no arguments.  Run once to start devbox.  Run again to terminate."""

import boto3
import time


def ec2_start(resource, role):
    """Start an AWS EC2 instance
    Arguments:
        resource = already open ec2 boto3.resource
        role     = puppet role to configure system as
    Returns:
        instances = boto3 collection of started instances"""

    # This userdata is specific for a Centos or Ubuntu devbox using standalone puppet
    userdata = ('#!/bin/sh\n'
                'mkdir -vp /etc/facter/facts.d\n'
                'echo "hostgroup=aws" > /etc/facter/facts.d/hostgroup.txt\n'
                'echo "role={0}" > /etc/facter/facts.d/role.txt\n'            # 0=role
                'if [ `which yum` ]; then yum -y install git; else apt-get update && apt-get install git; fi\n'
                'git clone https://github.com/peterezzo/petenet-puppet.git /etc/puppet\n'
                '/bin/sh /etc/puppet/support_scripts/bootstrap-puppet.sh\n').format(role)

    # Centos7 ImageId = ami-6d1c2007
    # RHEL ImageId = ami-2051294a
    # Ubuntu 14.04 ImageID = ami-fce3c696
    instances = resource.create_instances(
        ImageId='ami-fce3c696',
        MinCount=1,
        MaxCount=1,
        InstanceType='t2.nano',
        SubnetId='subnet-2fd98359',
        SecurityGroupIds=['sg-c7cc19bc'],
        KeyName='macbook',
        UserData=userdata,
        BlockDeviceMappings=[
            {
                'DeviceName': '/dev/sda1',  # root for ami, sometimes /dev/xvdh ?
                'Ebs': {
                    'VolumeSize': 20,
                    'DeleteOnTermination': True,
                    'VolumeType': 'gp2'
                },
            },
        ]
    )

    # not sure if we really need to sleep before tagging but see this often
    # and we wait until running which takes much longer than 1 second
    time.sleep(1)
    for instance in instances:
        instance.create_tags(
            Resources=[instance.id],
            Tags=[
                {
                    'Key': 'Role',
                    'Value': role
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


def main():
    """This is the main body of the program
    Arguments:
        None (yet)
    Returns:
        None"""

    # open ec2 connection
    ec2 = boto3.resource('ec2')
    instances = ec2.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])

    role = "devbox"  # hardcode for now

    # get a count of the running instances, stop all if any running, start if not
    # supposedly this sum does not load the whole collection in memory
    count = sum(1 for _ in instances)
    if count == 0:
        print("No instances running, starting up")
        new_instances = ec2_start(ec2, role)
        for instance in new_instances:
            # public IP is only allocated when system is running
            instance.wait_until_running()
            instance.load()
            print("id: {0}\naddress: {1}".format(instance.id, instance.public_ip_address))
    else:
        print(count, "instances running, killing VM(s)")
        for instance in instances:
            ec2_stop(ec2, instance.id)


# this is the start of the program
if __name__ == "__main__":
    main()
