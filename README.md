Overview
--------
This is the OpenStack LBaaS(Load Balancer as a Service) driver for SEnginx. It can provide basic load balancing service for OpenStack instances.

This driver is implemented based on OpenStack's haproxy driver in Havana version. The latest version of haproxy driver in OpenStack has been changed to support multi-vendor.

How To Use
----------
Only supports Havana version.

##### 1. Download and install the SEnginx LBaaS driver on both network node and controller node:

      git clone https://github.com/NeusoftSecurity/SEnginx-LBaaS-Driver
      cd SEnginx-LBaaS-Driver
      python setup.py install

##### 2. On your controller node:
   
   i. modify /etc/neutron/neutron.cf, add the following line:

      service_provider=LOADBALANCER:SEnginx:senginx.plugin_driver.SEnginxOnHostPluginDriver:default
   
   and comment out the original haproxy settings in neutron.cf.

   ii. restart neutron server:

      service neutron-server restart

##### 3. On your network node:
   
   i. modify /etc/neutron/neutron.cf, add the following line:

      service_provider=LOADBALANCER:SEnginx:senginx.plugin_driver.SEnginxOnHostPluginDriver:default

   and comment out the original haproxy settings.

   ii. modify /etc/neutron/lbaas-agent.ini, add the following line:

      device_driver = senginx.namespace_driver.SEnginxNSDriver

   and comment out the original haproxy settings.

   iii. modify /usr/bin/neutron-lbaas-agent as:

      from senginx.agent import main

   and also comment out the original haproxy settings.
   
   iv. restart neutron services:

      service neutron-server restart
      service neutron-lbaas-agent restart

##### 4. Don't forget to turn on LBaaS in OpenStack Horizon.

Limitation
----------
Current version of this driver has some limitations:

1. SEnginx's doesn't support source ip persistence method, so it's not functional in Horizon;

2. If a vip's protocol is set to "HTTPS", SEnginx will use tcp protocol to proxy the traffic. This is because SEnginx can't offload SSL traffic without certificates assigned;

3. Currently, the driver's get_stats method is no implemented. 
