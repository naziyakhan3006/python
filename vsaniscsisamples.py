#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Copyright 2016-2017 VMware, Inc.  All rights reserved.

This file includes sample codes for VC and ESXi sides vSAN iSCSI API accessing.

To provide an example of vSAN iSCSI API access, it shows how to enable vSAN
iSCSI service, create targets and LUNs, together with disable iSCSI service.

NOTE: using vSAN iSCSI target service API requires a minimal
vim.version.version11 Stub.

"""

__author__ = 'VMware, Inc'

from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import pbm, VmomiSupport, SoapStubAdapter, vim
import sys
import ssl
import atexit
import argparse
import getpass
from distutils.version import StrictVersion

#import the vSAN API python bindings
import vsanmgmtObjects
import vsanapiutils

def GetArgs():
   """
   Supports the command-line arguments listed below.
   """
   parser = argparse.ArgumentParser(
       description='Process args for vSAN iSCSI SDK sample application')
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

def connectToSpbm(stub, context):
   hostname = stub.host.split(":")[0]
   sessionCookie = stub.cookie.split('"')[1]
   VmomiSupport.GetRequestContext()["vcSessionCookie"] = sessionCookie

   pbmStub = SoapStubAdapter(
         host=hostname,
         path = "/pbm/sdk",
         version = "pbm.version.version2",
         sslContext=context,
         )
   pbmStub.cookie = stub.cookie
   pbmSi = pbm.ServiceInstance("ServiceInstance", pbmStub)
   return pbmSi

def getVsanStoragePolicy(pbmSi):
   resourceType = pbm.profile.ResourceType(
      resourceType=pbm.profile.ResourceTypeEnum.STORAGE
   )

   profileManager = pbmSi.RetrieveContent().profileManager
   profileIds = profileManager.PbmQueryProfile(resourceType)
   profiles = profileManager.PbmRetrieveContent(profileIds)
   for profile in profiles:
      # vSAN default storage profile possesses a unique profile ID of
      # 'aa6d5a82-1c88-45da-85d3-3d74b91a5bad' across different releases.
      # Other profiles may also be looked up when needed to apply to vSAN
      # iSCSI services.
      profileId = profile.profileId.uniqueId
      if (isinstance(profile, pbm.profile.CapabilityBasedProfile) and
            profileId == 'aa6d5a82-1c88-45da-85d3-3d74b91a5bad'):
         return vim.VirtualMachineDefinedProfileSpec(profileId=profileId)
   return None

def main():
   args = GetArgs()
   if args.password:
      password = args.password
   else:
      password = getpass.getpass(prompt='Enter password for host %s and '
                                        'user %s: ' % (args.host,args.user))

   # For python 2.7.9 and later, the default SSL context has more strict
   # connection handshaking rule. We may need turn of the hostname checking
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

   # For detecting whether the host is vCenter or ESXi.
   aboutInfo = si.content.about
   apiVersion = vsanapiutils.GetLatestVmodlVersion(args.host)

   if aboutInfo.apiType == 'VirtualCenter':
      vcVersion = StrictVersion(aboutInfo.apiVersion)
      if vcVersion < StrictVersion('6.5'):
         print('The Virtual Center with version %s (lower than 6.5) is not ',
               'supported.' % aboutInfo.apiVersion)
         return -1
   else:
      print('The vSAN iSCSI service API are only accessible through ',
            'vCenter')
      return -1

   cluster = getClusterInstance(args.clusterName, si)
   if cluster is None:
      print("Cluster %s is not found for %s" % (args.clusterName, args.host))
      return -1

   vcMos = vsanapiutils.GetVsanVcMos(si._stub, context=context,
                                     version=apiVersion)
   vits = vcMos['vsan-cluster-iscsi-target-system']
   vccs = vcMos['vsan-cluster-config-system']

   # Fetch the storage policy ID for enable vSAN iSCSI service and
   # create the iSCSI home object.
   pbmSi = connectToSpbm(si._stub, context)
   vsanStoragePolicy = getVsanStoragePolicy(pbmSi)
   if vsanStoragePolicy is None:
      print('Cannot find the vSAN Storage Policy from the Virtual ' +
            'Center server.')
      return -1

   # Enable iSCSI service through vSAN Cluster Reconfiguration API on VC, and
   # the config port defaults to 3260 and can be customized.
   defaultVsanConfigSpec = vim.cluster.VsanIscsiTargetServiceDefaultConfigSpec(
                                 networkInterface="vmk0",
                                 port=2300)
   vitEnableSpec = vim.cluster.VsanIscsiTargetServiceSpec(
                         homeObjectStoragePolicy=vsanStoragePolicy,
                         defaultConfig=defaultVsanConfigSpec,
                         enabled=True)

   clusterReconfigSpec = vim.vsan.ReconfigSpec(iscsiSpec=vitEnableSpec)
   vitEnableVsanTask = vccs.ReconfigureEx(cluster, clusterReconfigSpec)
   vitEnableVcTask = vsanapiutils.ConvertVsanTaskToVcTask(
                           vitEnableVsanTask, si._stub)
   vsanapiutils.WaitForTasks([vitEnableVcTask], si)
   print('Enable vSAN iSCSI service task finished with status: %s' %
         vitEnableVcTask.info.state)

   # Create vSAN iSCSI targets and an associated LUN with the size of 1GB.
   targetAlias = "sampleTarget"
   targetSpec = vim.cluster.VsanIscsiTargetSpec(
                      alias=targetAlias,
                      iqn='iqn.2015-08.com.vmware:vit.target1')
   vsanTask = vits.AddIscsiTarget(cluster, targetSpec)
   vcTask = vsanapiutils.ConvertVsanTaskToVcTask(vsanTask, si._stub)
   vsanapiutils.WaitForTasks([vcTask], si)
   print('Create vSAN iSCSI target task finished with status: %s' %
         vcTask.info.state)

   lunSize = 1 * 1024 * 1024 * 1024 # 1GB
   lunSpec = vim.cluster.VsanIscsiLUNSpec(
                                          lunId=0,
                                          lunSize=lunSize,
                                          storagePolicy=vsanStoragePolicy)
   vsanTask = vits.AddIscsiLUN(cluster, targetAlias, lunSpec)
   vcTask = vsanapiutils.ConvertVsanTaskToVcTask(vsanTask, si._stub)
   vsanapiutils.WaitForTasks([vcTask], si)
   print('Create vSAN iSCSI LUN task finished with status: %s' %
         vcTask.info.state)

   # Remove vSAN iSCSI targets and LUN associated with the targets.
   vsanTask = vits.RemoveIscsiLUN(cluster, targetAlias, 0)
   vcTask = vsanapiutils.ConvertVsanTaskToVcTask(vsanTask, si._stub)
   vsanapiutils.WaitForTasks([vcTask], si)
   print("Remove vSAN iSCSI LUN task finished with status:%s" %
         vcTask.info.state)

   vsanTask = vits.RemoveIscsiTarget(cluster, targetAlias)
   vcTask = vsanapiutils.ConvertVsanTaskToVcTask(vsanTask, si._stub)
   vsanapiutils.WaitForTasks([vcTask], si)
   print("Remove vSAN iSCSI target task finished with status:%s" %
        vcTask.info.state)

   # Disable iSCSI service through vSAN iSCSI API on vCenter.
   vitDisableSpec = vim.cluster.VsanIscsiTargetServiceSpec(enabled=False)
   clusterReconfigSpec = vim.vsan.ReconfigSpec(iscsiSpec=vitDisableSpec)
   vitDisableVsanTask = vccs.ReconfigureEx(cluster, clusterReconfigSpec)
   vitDisableVcTask = vsanapiutils.ConvertVsanTaskToVcTask(
                           vitDisableVsanTask, si._stub)
   vsanapiutils.WaitForTasks([vitDisableVcTask], si)
   print('Disable vSAN iSCSI service task finished with status: %s' %
         vitDisableVcTask.info.state)

if __name__ == "__main__":
   main()
