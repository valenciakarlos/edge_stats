import paramiko
import csv
import re

def ssh_and_collect_stats(hostname, port, username, password, output_csv):
    command = 'get logical-router interface stats | find "interface|name|RX|TX|Frag"'

    # Establish SSH connection
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname, port=port, username=username, password=password)

    stdin, stdout, stderr = client.exec_command("su - admin")
    stdin, stdout, stderr = client.exec_command(command)
    output = stdout.read().decode()

    client.close()

    # Parse the output
    interfaces = []
    current_interface = {}

    for line in output.splitlines():
        if line.strip().startswith('interface'):
            if current_interface:
                interfaces.append(current_interface)
                current_interface = {}
            current_interface['interface'] = line.split(':', 1)[-1].strip()
        elif line.strip().startswith('name'):
            current_interface['name'] = line.split(':', 1)[-1].strip()
        else:
            match = re.match(r'^\s*(\S[\w\- ]*?)\s*:\s*(\d+)', line)
            if match:
                key = match.group(1).strip().replace(' ', '_')
                value = match.group(2).strip()
                current_interface[key] = value

    if current_interface:
        interfaces.append(current_interface)

    # Write to CSV
    all_keys = set(k for iface in interfaces for k in iface.keys())
    fieldnames = ['interface', 'name'] + sorted(all_keys - {'interface', 'name'})

    with open(output_csv, mode='w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for iface in interfaces:
            writer.writerow(iface)

# Example usage
ssh_and_collect_stats(
    hostname='10.63.107.201',
    port=22,
    username='root',
    password='N294-adminadmin1',
    output_csv='router_interface_stats.csv'
)

