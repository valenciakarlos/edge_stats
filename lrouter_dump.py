#!/usr/bin/env python3
# ***************************************************************************
# Copyright 2019-2025 VMware, Inc.  All rights reserved. VMware Confidential.
# ***************************************************************************
# $Id$
# $DateTime$
# $Change$
# $Author$
# ***************************************************************************
#
import argparse
import json
import multiprocessing
import os
import subprocess
import sys
import time

sys.path.append("/opt/vmware/nsx-common/python")
import nsx_logging

# Script version
# This script is supported in releases before 4.2.x via manual installation.
# To ensure that GSS picks the latest changes, we introduce versioning to this script.
# Steps: https://knowledge.broadcom.com/external/article/393772
__version__ = "2.0.0"

# in seconds
INTERVAL = 20
CORE_PPS_DELAY = 0.1
# collect 1 sample of % usage for all CPU cores for next INTERVAL seconds
GET_CPU_USAGE_CMD = 'mpstat -P 0-{} -u {} 1'
DP_CONFIG_FILE = '/config/vmware/edge/config.json'
CPU_USAGE_JSON_FILE = '/var/run/vmware/edge/cpu_usage.json'
DP_STATS_LOG_FILE = '/var/log/vmware/dp-stats.log'
EDGE_APPCTL = "/usr/local/bin/edge-appctl"
DPD_CTL = "/var/run/vmware/edge/dpd.ctl"

nsx_logging.basicConfig(syslog=True, subcomp="dp-stats-collection")
logging = nsx_logging.getLogger(__name__)
logging.setLevel(nsx_logging.INFO)

is_bare_metal_edge = False

def invoke_command(cmd_list, shell=False):
    """Invoke a command.
    Args:
        cmd_list: List containing command to run and arguments to pass,
                  (e.g ["ps", "aux"]).
    Return:
        returncode, output, error
    """
    out_stream = subprocess.PIPE
    p = subprocess.Popen(cmd_list,
                         stdout=out_stream,
                         stderr=subprocess.PIPE,
                         shell=shell)
    out, err = p.communicate()

    if sys.version_info >= (3,):
        if out is not None:
            out = out.decode("utf8")
        if err is not None:
            err = err.decode("utf8")

    rc = p.returncode
    return rc, out, err


def invoke_dpctl(cmd_list):
    """Invoke a command to datapath

    Args:
        cmd_list: List containing command and arguments to pass to appctl,
                  (e.g ["lswitch/show", "stats"]).
    Retrun:
        else: (returncode, output, error)
    """
    if not os.path.exists(EDGE_APPCTL):
        return -1, "", ""
    if not os.path.exists(DPD_CTL):
        return -1, "", ""

    cmds = [EDGE_APPCTL, "-t", DPD_CTL]
    if isinstance(cmd_list, list):
        cmds += cmd_list
    else:  # string
        cmds.append(cmd_list)
    return invoke_command(cmds)


def get_non_dp_polling_cores():
    # Get a list of non-datapath cores that are used by processes that poll on BM Edge.
    # This includes kni_single and lb-dispatcher threads.
    if not is_bare_metal_edge:
        return []
    poll_cores = []
    for proc in ["kni_single", "lb-dispatcher"]:
        cmd = "taskset -pc $(pidof " + proc + ")"
        rc, out, err = invoke_command(cmd, shell=True)
        if rc == 0:
            # Examples of command output. These two processes are expect to have affinity
            # for a single core.
            # taskset -pc $(pidof kni_single)
            # pid 1138869's current affinity list: 25
            # taskset -pc $(pidof lb-dispatcher)
            # pid 1138064's current affinity list: 24
            try:
                core = int(out.split(":")[1].strip())
                poll_cores.append(core)
            except Exception as e:
                pass
    return poll_cores


def get_all_cpu_core_usage(num_cores, interval):
    cores_usage_list = []
    global GET_CPU_USAGE_CMD
    timeout = int(interval * 0.75)
    out = ""
    # Spend 75% of interval time in mpstat to get the CPU usage
    with os.popen(GET_CPU_USAGE_CMD.format(num_cores - 1, timeout)) as out_popen:
        out = out_popen.read().strip()

    # core usage data will be in last n lines
    lines = out.split('\n')[-num_cores:]
    for line in lines:
        idle_percent = line.split()[-1]
        cores_usage_list.append(100 - float(idle_percent))
    return cores_usage_list


def get_dp_core_pps(rx_pps, tx_pps, core_usage):
    # We avoid cpuusage/show as it blocks dp-ipc thread, instead we use
    # cpuusage/start and cpuusage/stop that has the same logic while being
    # non-blocking.
    rc, out, err = invoke_dpctl("cpuusage/start")
    if rc != 0:
        return False

    # 100ms is used as delay to mimic cpuusage/show
    time.sleep(CORE_PPS_DELAY)
    rc, out, err = invoke_dpctl("cpuusage/stop")
    if rc != 0:
        return False
    out = json.loads(out)
    for cpu in out:
        core_id = cpu.get("core", 0)
        rx_pps[core_id] = cpu.get("rx", 0)
        tx_pps[core_id] = cpu.get("tx", 0)
        core_usage[core_id] = cpu.get("usage", 0)
    return True


def read_dp_config_file():
    global DP_CONFIG_FILE
    config = {}
    try:
        with open(DP_CONFIG_FILE) as f:
            config = json.load(f)
    except Exception as e:
        # DP is not UP yet, so we exit immediately
        exit(1)
    return config


def get_dpdk_corelist(config):
    dpdk_corelist = []

    # eg. "corelist": "0,1,2,3,4,5",
    corelist = config.get("corelist", '')
    dpdk_corelist = [int(i) for i in corelist.split(',')]
    return dpdk_corelist


def get_service_corelist(config):
    service_corelist = []

    if "service_corelist" in config.get("custom", ''):
        corelist = config.get("custom", '').get("service_corelist", '')
    else:
        corelist = config.get("service_corelist", '')
    service_corelist = [int(i) for i in corelist.split(',')]
    return service_corelist


def get_dpdk_cores_cycles_from_dp(dpdk_cores_cycles):
    rc, out, err = invoke_dpctl("cpuusage/cycles")
    if rc != 0:
        return False
    out = json.loads(out)
    for cpu in out:
        cycles = {}
        core_id = cpu.get("core", 0)
        cycles["busy"] = cpu.get("busy_cycles", 0)
        cycles["idle"] = cpu.get("idle_cycles", 0)
        dpdk_cores_cycles[core_id] = cycles
    return True


def get_micro_flow_cache_hit_rate(hit_rates):
    rc, out, err = invoke_dpctl("flow_cache/micro/show")
    if rc != 0:
        return
    flow_cache_out = json.loads(out)
    for per_core_entry in flow_cache_out:
        core_id = per_core_entry['core']
        hit_rates[core_id] = per_core_entry['hit rate']
    return


def get_mega_flow_cache_hit_rate(hit_rates):
    rc, out, err = invoke_dpctl("flow_cache/mega/show")
    if rc != 0:
        return
    flow_cache_out = json.loads(out)
    for per_core_entry in flow_cache_out:
        core_id = per_core_entry['core']
        hit_rates[core_id] = per_core_entry['hit rate']
    return


def get_physical_port_stats(phy_port_stats):
    rc, out, err = invoke_dpctl(["physical_port/show", "stats"])
    if rc != 0:
        return
    phy_port_stats += json.loads(out)
    return


def get_lrouter_port_stats(lrouter_port_stats):
    rc, out, err = invoke_dpctl(["lrouter_port/show"])
    if rc != 0:
        return
    lrouter_port_stats += json.loads(out)
    return

def generate_dp_stats_json_file(dpdk_cores_usage, non_dpdk_cores_usage,
                                micro_hit_rates, mega_hit_rates, rx_pps,
                                tx_pps, phy_port_stats, service_corelist,
                                core_usages, enable_log=False, enable_json=False):
    result = {}
    result["dpdk_cpu_cores"] = len(dpdk_cores_usage)
    result["non_dpdk_cpu_cores"] = len(non_dpdk_cores_usage)

    dpdk_cores_usage_list = list(dpdk_cores_usage.values())
    if len(dpdk_cores_usage_list) > 0:
        result["highest_cpu_core_usage_dpdk"] = round(max(dpdk_cores_usage_list), 2)
        result["avg_cpu_core_usage_dpdk"] = round(sum(dpdk_cores_usage_list)/len(dpdk_cores_usage_list), 2)
    else:
        result["highest_cpu_core_usage_dpdk"] = 0
        result["avg_cpu_core_usage_dpdk"] = 0

    non_dpdk_cores_usage_list = list(non_dpdk_cores_usage.values())
    if len(non_dpdk_cores_usage_list) > 0:
        # On BME the kni-single and lb-dispatcher threads are in polling mode and as such
        # are expected to be at 100% usage always. This messes up the average CPU usage for
        # non-dpdk cpu cores, so exclude these cores from the average calculation and max usage.
        poll_cores = get_non_dp_polling_cores()
        temp = list(set(non_dpdk_cores_usage).difference(poll_cores))
        filtered_list = [non_dpdk_cores_usage[i] for i in temp]
        result["highest_cpu_core_usage_non_dpdk"] = round(max(filtered_list), 2)
        result["avg_cpu_core_usage_non_dpdk"] = round(sum(filtered_list)/len(filtered_list), 2)
    else:
        result["highest_cpu_core_usage_non_dpdk"] = 0
        result["avg_cpu_core_usage_non_dpdk"] = 0

    result["dpdk_cpu_per_core"] = {}
    for core_id in dpdk_cores_usage:
        result["dpdk_cpu_per_core"][core_id] = round(dpdk_cores_usage[core_id], 2)
    result["micro_hit_rates"] = {}
    result["mega_hit_rates"] = {}
    for core_id in mega_hit_rates:
        result["mega_hit_rates"][core_id] = mega_hit_rates[str(core_id)]
    for core_id in micro_hit_rates:
        result["micro_hit_rates"][core_id] = micro_hit_rates[str(core_id)]

    result["non_dpdk_cpu_per_core"] = {}
    for core_id in non_dpdk_cores_usage:
        result["non_dpdk_cpu_per_core"][core_id] = round(non_dpdk_cores_usage[core_id], 2)

    result["rx_pps"] = {}
    for core_id in rx_pps:
        result["rx_pps"][core_id] = rx_pps[core_id]

    result["tx_pps"] = {}
    for core_id in tx_pps:
        result["tx_pps"][core_id] = tx_pps[core_id]

    result["micro_core_usages"] = {}
    for core_id in core_usages:
        result["micro_core_usages"][core_id] = core_usages[core_id]

    result["service_corelist"] = {}
    for core_id in service_corelist:
        result["service_corelist"]['cpu' + str(core_id)] = core_id

    try:
        if enable_json:
            with open(CPU_USAGE_JSON_FILE, 'w') as f:
                json.dump(result, f, indent=4)
    except FileNotFoundError:
        # Handle the case where the directory does not exist
        logging.error("Directory doesn't exist to create file {}".format(CPU_USAGE_JSON_FILE))
    except Exception as e:
        # Catch any other exceptions
        logging.error("Failed to write to file {}, error: {}".format(CPU_USAGE_JSON_FILE, e))

    try:
        if enable_log:
            # Trim cpu usage to have only useful info
            remove_entries = [
                "dpdk_cpu_cores",
                "non_dpdk_cpu_cores",
                "service_corelist"
            ]
            for key in remove_entries:
                result.pop(key, None)

            # Log it too, rotation logic will be applied
            with open(DP_STATS_LOG_FILE, 'a') as f:
                json_data = {}
                current_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                json_data["timestamp"] = current_timestamp
                json_data["cpu_usage"] = result
                json_data["phy_port_stats"] = phy_port_stats
                json.dump(json_data, f, indent=4)
                f.write("\n")
    except FileNotFoundError:
        # Handle the case where the directory does not exist
        logging.error("Directory doesn't exist to create file {}".format(DP_STATS_LOG_FILE))
    except Exception as e:
        # Catch any other exceptions
        logging.error("Failed to append to file {}, error: {}".format(DP_STATS_LOG_FILE, e))


def parseargs():
    parser = argparse.ArgumentParser(description='''
        Script to collect Edge Datapath statistics and dump to a JSON file.
    ''')
    parser.add_argument('--interval', '-i', default=INTERVAL, choices=range(5, 120),
                        type=int, metavar="[5-120]",
                        help='Interval in seconds (Default: %(default)s)')
    parser.add_argument('--enable-log', action="store_true", help='Dump to dp_stats.log')
    parser.add_argument('--enable-json', action="store_true", help='Dump to cpu_usage.json')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}',
                        help='Display the version of the script.')
    return parser.parse_args()


def main():
    args = parseargs()
    start_dpdk_cores_cycles = {}
    end_dpdk_cores_cycles = {}
    micro_hit_rates = {}
    mega_hit_rates = {}
    rx_pps = {}
    tx_pps = {}
    core_usages = {}
    phy_port_stats = []
    lrouter_port_stats = []
    dp_config = {}

    # Read DP configuration
    dp_config = read_dp_config_file()
    global is_bare_metal_edge
    is_bare_metal_edge = dp_config.get('is_bare_metal_edge', False)

    # get dpdk cpu cycles at start of interval
    dpdk_cycles_success = get_dpdk_cores_cycles_from_dp(start_dpdk_cores_cycles)
    num_cores = multiprocessing.cpu_count()
    # this call will block for the interval
    cores_usage_list = get_all_cpu_core_usage(num_cores, args.interval)
    if dpdk_cycles_success:
        # get dpdk cpu cycles at end of interval
        dpdk_cycles_success = get_dpdk_cores_cycles_from_dp(end_dpdk_cores_cycles)

    # Get flow cache hit rate
    get_micro_flow_cache_hit_rate(micro_hit_rates)
    get_mega_flow_cache_hit_rate(mega_hit_rates)

    # Get per core pps
    get_dp_core_pps(rx_pps, tx_pps, core_usages)

    # Get physical port stats
    get_physical_port_stats(phy_port_stats)

    # Get lrouter port stats (stats per logical interface including frangmentation)
    get_lrouter_port_stats(lrouter_port_stats)
    print(lrouter_port_stats)


    '''
    all_cores = list(range(num_cores))
    dpdk_corelist = get_dpdk_corelist(dp_config)
    service_corelist = get_service_corelist(dp_config)
    dpdk_corelist = list(set(dpdk_corelist) - set(service_corelist))
    non_dpdk_corelist = list(set(all_cores) - set(dpdk_corelist))
    dpdk_cores_usage = {}
    non_dpdk_cores_usage = {}

    for core_id in dpdk_corelist:
        if dpdk_cycles_success:
            # calculate the usage from raw data
            idle_cycles = end_dpdk_cores_cycles[core_id]["idle"] - start_dpdk_cores_cycles[core_id]["idle"]
            busy_cycles = end_dpdk_cores_cycles[core_id]["busy"] - start_dpdk_cores_cycles[core_id]["busy"]
            dpdk_cores_usage[core_id] = float(busy_cycles)/(idle_cycles + busy_cycles) * 100
        elif not is_bare_metal_edge:
            # edge is in interrupt mode (Edge VM), use data from Linux
            dpdk_cores_usage[core_id] = cores_usage_list[core_id]
        else:
            # edge is in polling mode (Baremetal Edge), skip this round
            dpdk_cores_usage[core_id] = 0
    for core_id in non_dpdk_corelist:
        non_dpdk_cores_usage[core_id] = cores_usage_list[core_id]
    generate_dp_stats_json_file(dpdk_cores_usage, non_dpdk_cores_usage,
                                micro_hit_rates, mega_hit_rates, rx_pps,
                                tx_pps, phy_port_stats, service_corelist,
                                core_usages, args.enable_log, args.enable_json)
    '''
    sys.exit(0)


if __name__ == "__main__":
    main()

