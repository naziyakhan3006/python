#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Copyright 2016-2017 VMware, Inc.  All rights reserved.

This file includes sample codes for vCenter and ESXi sides vSAN API accessing.

To provide an example of vCenter side vSAN API access, it shows how to get vSAN
cluster health status by invoking the QueryClusterHealthSummary() API of the
VsanVcClusterHealthSystem MO.

To provide an example of ESXi side vSAN API access, it shows how to get
performance server related host information by invoking the
VsanPerfQueryNodeInformation() API of the VsanPerformanceManager MO.

"""

__author__ = 'VMware, Inc'

from pyVim.connect import SmartConnect, Disconnect
import sys
import ssl
import atexit
import argparse
import getpass
if sys.version[0] < '3':
    input = raw_input

# Import the vSAN API python bindings and utilities.
import vsanmgmtObjects
import vsanapiutils

def GetArgs():
    """
    Supports the command-line arguments listed below.
    """
    parser = argparse.ArgumentParser(
        description='Process args for vSAN SDK sample application')
    parser.add_argument('-s', '--host', required=True, action='store',
                        help='Remote host to connect to')
    parser.add_argument('-o', '--port', type=int, default=443, action='store',
                        help='Port to connect on')
    parser.add_argument('-u', '--user', required=True, action='store',
                        help='User name to use when connecting to host')
    parser.add_argument('-p', '--password', required=False, action='store',
                        help='Password to use when connecting to host')
    parser.add_argument('--cluster', dest='clusterName', metavar="CLUSTER",
                        default='VSAN-Cluster')
    args = parser.parse_args()
    return args

def getClusterInstance(clusterName, serviceInstance):
    content = serviceInstance.RetrieveContent()
    searchIndex = content.searchIndex
    datacenters = content.rootFolder.childEntity
    for datacenter in datacenters:
        cluster = searchIndex.FindChild(datacenter.hostFolder, clusterName)
        if cluster is not None:
            return cluster
    return None

def main():
    args = GetArgs()
    if args.password:
        password = args.password
    else:
        password = getpass.getpass(prompt='Enter password for host %s and '
                                          'user %s: ' % (args.host,args.user))

    # For python 2.7.9 and later, the default SSL context has more strict
    # connection handshaking rule. We may need turn off the hostname checking
    # and client side cert verification.
    context = None
    if sys.version_info[:3] > (2,7,8):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    si = SmartConnect(host=args.host,
                      user=args.user,
                      pwd=password,
                      port=int(args.port),
                      sslContext=context)

    atexit.register(Disconnect, si)

    # Detecting whether the host is vCenter or ESXi.
    aboutInfo = si.content.about
    apiVersion = vsanapiutils.GetLatestVmodlVersion(args.host)

    if aboutInfo.apiType == 'VirtualCenter':
        majorApiVersion = aboutInfo.apiVersion.split('.')[0]
        if int(majorApiVersion) < 6:
            print('The Virtual Center with version %s (lower than 6.0) is not supported.'
                  % aboutInfo.apiVersion)
            return -1

        # Get vSAN health system from the vCenter Managed Object references.
        vcMos = vsanapiutils.GetVsanVcMos(
            si._stub, context=context, version=apiVersion)
        vhs = vcMos['vsan-cluster-health-system']

        cluster = getClusterInstance(args.clusterName, si)

        if cluster is None:
            print("Cluster %s is not found for %s" % (args.clusterName, args.host))
            return -1

        # vSAN cluster health summary can be cached at vCenter.
        fetchFromCache = False
        healthSummary = vhs.QueryClusterHealthSummary(
            cluster=cluster, includeObjUuids=True, fetchFromCache=fetchFromCache)

        clusterStatus = healthSummary.clusterStatus
        groups = healthSummary.groups
        foundFailure = False
        import pprint
        for group in groups:
            if group.groupName == 'com.vmware.vsan.health.test.cluster':
                for test in group.groupTests:
                    if test.testHealth != 'green':
                        foundFailure = True
                        break

        if not foundFailure:
            for hostStatus in clusterStatus.trackedHostsStatus:
                if hostStatus.status != 'green':
                    foundFailure = True
                    break

        pprint.pprint(foundFailure)

        return foundFailure

if __name__ == "__main__":
    main()
