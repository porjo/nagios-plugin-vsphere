#!/usr/bin/env python
#
# Code based on: https://github.com/mzupan/nagios-plugin-vsphere
#
# Example usage:
#
#  $ ./check_vsphere.py -H 10.1.1.1 -u admin -p guessme -A datastore
#
# -A is one of the following
#
# Action 'general_health':
# -----------------------------------------
# Checks the 'overallStatus' register for each host. 
# Thresholds are ignored. Instead, a critical alert is issued if errors are returned
# The following statuses are possible:
#
# gray		The status is unknown.
# green		The entity is OK.
# red		The entity definitely has a problem.
# yellow	The entity might have a problem. 
#
#
# Action 'connect':
# -----------------------------------------
# Will alert if the time taken to connect to the server exceeds thresholds:
#
#  -W <seconds> (3)
#  -C <seconds> (6)
#
# Action 'datastore':
# -----------------------------------------
# Checks each datastore in the VirtualCenter, alerting where thresholds exceed:
#
#  -W <GiB free#Percent Free> (100#10)
#  -C <GiB free#Percent Free> (50#5)
#
# Alert will only show if both conditions are met:
# a) GiB free is less than the GiB threshold 
# b) percentage free is less than the % threshold
#
# e.g. with a warning threshold of 100#10:
# - host with 10Gig free on a 50Gig datastore will NOT alert (20% free is greater than 10% threshold)
# - host with 10Gig free on a 500Gig datastore WILL alert (2% free is less than 10% threshold)
#

import sys
import optparse
import time

try:
    from pysphere import VIServer, MORTypes, VIProperty
except ImportError, e:
    print e
    sys.exit(2)

def main(argv):
    p = optparse.OptionParser(conflict_handler="resolve", description= "This Nagios plugin checks the health of vmware hosts.")

    p.add_option('-H', '--host', action='store', type='string', dest='host', default='127.0.0.1', help='The hostname you want to connect to')
    p.add_option('-u', '--user', action='store', type='string', dest='user', default=None, help='The username you want to login as')
    p.add_option('-p', '--pass', action='store', type='string', dest='passwd', default=None, help='The password you want to use for that user')
    p.add_option('-A', '--action', action='store', type='choice', dest='action', default='connect', help='The action you want to take',
                 choices=['connect', 'general_health', 'datastore'])
    p.add_option('-W', '--warning', action='store', dest='warning', default=None, help='The warning threshold we want to set')
    p.add_option('-C', '--critical', action='store', dest='critical', default=None, help='The critical threshold we want to set')
    options, arguments = p.parse_args()

    host = options.host
    user = options.user
    user = user.replace('\\\\','\\') #fixup <domain\user> syntax
    passwd = options.passwd
    action = options.action
   
    if action != "datastore":
        warning = float(options.warning or 0) 
        critical = float(options.critical or 0) 
    else:
        warning = options.warning or ''
        critical = options.critical or ''

    if user is None:
        print "You need to enter a username"
        sys.exit(2)
        if passwd is None:
            print "You need to enter a password"
            sys.exit(2)

    #
    # always make the connection first
    server = VIServer()
    start = time.time()
    try:
        server.connect(host, user, passwd)
    except Exception, e:
        print "Connection to VSphere failed: ", e
        sys.exit(2)
    conn_time = time.time() - start
    conn_time = round(conn_time, 0)

    if action == "general_health":
        return general_health(server)
    elif action == "datastore":
        return datastore(server, warning, critical)
    else:
        return check_connect(conn_time, warning, critical)


def performance_data(params):
    data= " |"
    for p in params:
        p += (None,None,None,None)
        param,param_name,warning,critical = p[0:4];
        data +=" %s=%s" % (param_name,str(param))    
        if warning or critical:
            warning = warning or 0
            critical = critical or 0
            data += ";%s;%s" % (warning,critical)
            return data

def numeric_type(param):
    if ((type(param) == float or type(param) == int or param == None)):
        return True
    return False

def check_levels(param, warning, critical,message,ok=[]):
    if (numeric_type(critical) and numeric_type(warning)):
        if param >= critical:
            print "CRITICAL - " + message
            return 2
        elif param >= warning:
            print "WARNING - " + message
            return 1
        else:
            print "OK - " + message
            return 0
    else:
        if param in critical:
            print "CRITICAL - " + message
            return 2

        if param in warning:
            print "WARNING - " + message
            return 1

        if param in ok:
            print "OK - " + message
            return 0

        # unexpected param value
        print "CRITICAL - Unexpected value : %d" % param + "; " + message
        return 2


def check_connect(conn_time, warning, critical):
    warning = warning or 3
    critical = critical or 6
    message = "Connection took %i seconds" % conn_time
    message += performance_data([(conn_time,"connection_time",warning,critical)])

    return check_levels(conn_time,warning,critical,message)

def general_health(server):
    pm = server.get_performance_manager()

    props = server._retrieve_properties_traversal(property_names=['name', 'summary.overallStatus'], obj_type="HostSystem")

    errors = []
    for prop_set in props:
        for prop in prop_set.PropSet:
            if prop.Name == "name":
                host = prop.Val
            elif prop.Name == "summary.overallStatus":
                #print "DEBUG: Host: %s is in state of %s" % (host, prop.Val)
                if prop.Val == "gray":
                    errors.append("Host: %s is in an unknown state (gray)" % host)
                elif prop.Val != "green":
                    errors.append("Host: %s is in state of %s" % (host, prop.Val))

    if len(errors) == 0:
        print "All hosts are green"
        sys.exit(0)
    else:
        for error in errors:
            print error

        sys.exit(2)

def datastore(server, warning, critical):

    bits = warning.split('#')
    warningGB = float(bits[0] or 100)  
    if len(bits) > 1:
        warningPC = float(bits[1] or 100)
    else:
        warningPC = float(100)

    bits = critical.split('#')
    criticalGB = float(bits[0] or 50)
    if len(bits) > 1:
         criticalPC = float(bits[1] or 100)
    else:
         criticalPC = float(100)

    #print "DEBUG: warn GB %f warn %% %f, crit GB %f crit %% %f" % (warningGB, warningPC, criticalGB, criticalPC)

    ds_by_dc = {}
    for dc_mor, dc_name in server.get_datacenters().items():
        p = VIProperty(server, dc_mor)
        for ds in p.datastore:
            ds_by_dc[ds._obj] = {'ds_name': '', 'dc_name': dc_name, 'capacity': 0, 'free': 0}

    properties = ["name","summary.capacity","summary.freeSpace"]
    results = server._retrieve_properties_traversal(property_names=properties,obj_type=MORTypes.Datastore)

    for item in results:
        for p in item.PropSet:
            if p.Name == 'name':
                ds_by_dc[item.Obj]['ds_name'] = p.Val
            elif p.Name == 'summary.capacity':
                ds_by_dc[item.Obj]['capacity'] = p.Val
            elif p.Name == 'summary.freeSpace':
                ds_by_dc[item.Obj]['free'] = p.Val

    warnings = []
    criticals = []
    for name in ds_by_dc:

        freePC = float(ds_by_dc[name]['free']) / float(ds_by_dc[name]['capacity']) * 100
        freeGB = float(ds_by_dc[name]['free']) / 1024 / 1024 / 1024

        #print "DEBUG: %s %s %.2f %.2f%%" % (ds_by_dc[name]['ds_name'], ds_by_dc[name]['dc_name'], freeGB, freePC)
    
        if freeGB < warningGB and freePC < warningPC:
            warnings.append( "%s [%s] has %.2f GiB disk space free (%.2f%%)" % (ds_by_dc[name]['ds_name'], ds_by_dc[name]['dc_name'], freeGB, freePC) )
        if freeGB < criticalGB and freePC < criticalPC:
            criticals.append( "%s [%s] has %.2f GiB disk space free (%.2f%%)" % (ds_by_dc[name]['ds_name'], ds_by_dc[name]['dc_name'], freeGB, freePC) )

    if len(criticals) > 0:
	for c in criticals:
            print c
	for w in warnings:
            print w
        sys.exit(2)
    elif len(warnings) > 0:
	for w in warnings:
            print w
        sys.exit(1)
    else:
        print "Datastores OK"
        sys.exit(0)

    print "No Datastores found, or other error"
    sys.exit(3)

#
# main app
#
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
