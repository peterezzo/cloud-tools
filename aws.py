#!/usr/bin/env python
"""This is a script that controls a specific AWS instance's lifecycle
   It currently accepts no arguments.  Run once to start devbox.  Run again to terminate."""

import boto3

def ec2_start(resource):
    """Start an AWS EC2 instance
       resource = already open ec2 boto3.resource"""

    # TODO: This userdata is specific for a Centos devbox using standalone puppet
    userdata = ('#!/bin/sh\n'
                'mkdir -p /etc/facter/facts.d\n'
                'echo "hostgroup=aws" > /etc/facter/facts.d/hostgroup.txt\n'  #used by puppet
                'rpm -ivh http://yum.puppetlabs.com/puppetlabs-release-el-7.noarch.rpm\n'
                'yes | yum -y install git puppet\n'
                'mv /etc/puppet /etc/puppet.repo\n'
                'git clone https://github.com/peterezzo/petenet-puppet.git /etc/puppet\n'
                'cp /etc/puppet.repo/{auth.conf,puppet.conf} /etc/puppet\n'
                'puppet apply /etc/puppet/manifests/site.pp\n')

    # Centos7 ImageId = ami-6d1c2007
    instance = resource.create_instances(
        ImageId='ami-6d1c2007',
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

    # instance.create_tags()
    # instance.delete_tags()
    # instance.console_output()
    # instance.modify_attribute()
    # instance.load()
    return instance


def ec2_stop(resource, instance_id):
    """Stop and terminate an AWS EC2 instance
       resource = already open ec2 boto3.resource
       instance_id = id of instance to terminate"""

    print("Terminating instance id", instance_id)
    resource.instances.filter(InstanceIds=[instance_id]).stop()
    resource.instances.filter(InstanceIds=[instance_id]).terminate()


def main():
    """This is the main body of the program
       Receives no arguments"""

    ec2 = boto3.resource('ec2')

    instances = ec2.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])

    # get a count of the running instances, stop all if any running, start if not
    # supposedly this sum does not load the whole collection in memory
    count = sum(1 for _ in instances)
    if count == 0:
        print("No instances running, starting VM")
        vm = ec2_start(ec2)
        for instance in vm:
            # public IP is only allocated when system is running
            instance.wait_until_running()
            instance.load()
            print("id", instance.id, "\naddress", instance.public_ip_address)
    else:
        print(count, "instances running, killing VM(s)")
        for instance in instances:
            ec2_stop(ec2, instance.id)


# this is the start of the program
if __name__ == "__main__":
    main()
