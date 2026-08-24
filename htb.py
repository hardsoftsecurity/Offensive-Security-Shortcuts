#!/usr/bin/env python3
#
# NOTE: Expects /htb/ folder. ( sudo mkdir /htb && sudo chown kali:kali /htb )
#
# This script integrates htb-cli to automate the HTB box setup workflow:
# - Check if we have a tun0 ip, if not it exits
# - Ask for the name of the box
# - Attempts to start the machine via htb-cli and extract its IP automatically
#   - If the machine is not found, retries every 30s until it appears (for Saturday releases)
#   - If IP extraction fails, falls back to manual IP input
# - Pings the box indefinitely until it responds, every 15 failed pings it will ask you if you want to:
#   - continue -> it will ping 15 more times and come back to the same prompt
#   - skip -> skip the ping step and just assume the box is up
#   - exit -> exit the script
# - Sets up tmux windows with the recon commands:
#   - If already inside a tmux session: adds new windows to the CURRENT session
#   - If not inside tmux: creates a new named session and attaches to it
# It then fullscreens the current i3 pane and attaches to this tmux session.

import os
import re
import subprocess
import sys
import time
import threading

FULLSCREEN_I3_PANE = "i3-msg '[con_id=\"__focused__\"] fullscreen enable'"

# ──────────────────────────────────────────────────────────────
# HTB-CLI INTEGRATION
# ──────────────────────────────────────────────────────────────

def htb_start_machine(machine_name):
    """
    Starts a machine via htb-cli, then polls 'htb-cli info' until a real IP
    is assigned (i.e. not 'Undefined'). Returns the IP string or None on failure.

    Background: 'htb-cli start' outputs "Target:" with nothing after it because
    HTB assigns the IP asynchronously after the machine finishes booting.
    We must poll info until the IP column is populated.
    """
    print(f"[*] Starting '{machine_name}' via htb-cli...")
    try:
        result = subprocess.run(
            ["htb-cli", "start", "--batch", "-m", machine_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180
        )
        output = result.stdout + result.stderr

        if result.returncode != 0:
            print(f"[-] htb-cli start failed (code {result.returncode})")
            print(f"    Output: {output.strip()}")
            return None

        if "deployed" not in output.lower() and "lab" not in output.lower():
            print(f"[-] Machine may not have started correctly.")
            print(f"    Output was: {output.strip()}")
            return None

        print(f"[+] Machine deployed! Waiting for IP to be assigned...")

    except FileNotFoundError:
        print("[-] htb-cli not found in PATH. Make sure it is installed.")
        return None
    except subprocess.TimeoutExpired:
        print("[-] htb-cli timed out waiting for machine to deploy.")
        return None
    except Exception as e:
        print(f"[-] Unexpected error running htb-cli start: {e}")
        return None

    # Poll htb-cli info until IP is no longer 'Undefined'
    max_attempts = 24   # 24 x 10s = 4 minutes max
    for attempt in range(1, max_attempts + 1):
        print(f"[*] Polling for IP... attempt {attempt}/{max_attempts}", end="\r")
        try:
            info = subprocess.run(
                ["htb-cli", "info", "--batch", "-m", machine_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180
            )
            info_output = info.stdout + info.stderr

            ip_match = re.search(r'\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b', info_output)
            if ip_match:
                ip = ip_match.group(1)
                print(f"\n[+] IP assigned: {ip}")
                return ip

        except Exception as e:
            print(f"\n[-] Error polling info: {e}")

        time.sleep(10)

    print(f"\n[-] IP was never assigned after {max_attempts} attempts.")
    return None


def htb_check_machine_exists(machine_name):
    """
    Uses 'htb-cli info --batch -m <n>' to verify the machine exists on HTB.
    Retries indefinitely every 30s to handle Saturday releases where the machine
    may not be listed yet at the moment the script is started.
    """
    attempt = 0
    while True:
        attempt += 1
        print(f"[*] Checking if '{machine_name}' exists on HTB (attempt {attempt})...", end="\r")
        try:
            result = subprocess.run(
                ["htb-cli", "info", "--batch", "-m", machine_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180
            )
            output = result.stdout + result.stderr

            if machine_name.lower() in output.lower():
                print(f"\n[+] Machine '{machine_name}' found on HTB.")
                return True

        except FileNotFoundError:
            print("\n[-] htb-cli not found in PATH.")
            return False
        except Exception as e:
            print(f"\n[-] Error checking machine existence: {e}")

        if attempt % 10 == 0:
            action = input(f"\n[!] '{machine_name}' not found yet after {attempt} attempts. (c)ontinue/(e)xit? ").strip().lower()
            if action == 'e':
                print("[+] Exiting.")
                sys.exit(0)
            print("[+] Continuing to check...")
        else:
            time.sleep(30)


def get_box_ip_via_htb_cli(machine_name):
    """
    Full htb-cli workflow:
      1. Check if machine exists (retries until found).
      2. Start machine and extract IP.
      3. On any failure, return None so the caller can fall back to manual input.
    """
    if not htb_check_machine_exists(machine_name):
        return None

    ip = htb_start_machine(machine_name)
    return ip


# ──────────────────────────────────────────────────────────────
# ORIGINAL HELPERS
# ──────────────────────────────────────────────────────────────

def check_tun0_interface():
    try:
        result = subprocess.run(
            ['ip', 'addr', 'show', 'tun0'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            print("[-] tun0 not found or not connected")
            sys.exit(1)
        for line in result.stdout.splitlines():
            if "inet " in line:
                ip_address = line.strip().split()[1].split('/')[0]
                return ip_address
        print("[-] tun0 found but no IP address assigned")
        sys.exit(1)
    except Exception as e:
        print(f"[-] Error checking tun0: {e}")
        sys.exit(1)


def create_directory(name):
    path = f"/home/david/hackTheBox/machines/{name}"
    try:
        os.makedirs(path, exist_ok=True)
        print(f"[+] Successfully created the directory: {path}")
    except PermissionError:
        print(f"[-] Permission denied: Cannot create directory at {path}")
        sys.exit(1)
    except OSError as e:
        print(f"[-] Failed to create the directory: {e}")
        sys.exit(1)


def run_zsh_command(command):
    try:
        result = subprocess.run(
            ['zsh', '-c', f'source ~/.zshrc && {command}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            print(f"[+] Command '{command}' executed successfully")
        else:
            print(f"[-] Command '{command}' failed to execute")
            print(result.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"[-] Error executing command '{command}': {e}")
        sys.exit(1)


def run_zsh_command_in_tmux_pane(session_name, pane, command):
    try:
        subprocess.run(
            ['tmux', 'send-keys', '-t', f'{session_name}:{pane}', command, 'C-m'],
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"[-] Failed to run command '{command}' in pane {pane}: {e}")
        sys.exit(1)


def get_current_tmux_session():
    tmux_env = os.environ.get('TMUX')
    if not tmux_env:
        return None
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', '#S'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_tmux_window_names(session_name):
    try:
        result = subprocess.run(
            ['tmux', 'list-windows', '-t', session_name, '-F', '#{window_name}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()
    except Exception:
        pass
    return []


def unique_window_name(session_name, base_name):
    existing = get_tmux_window_names(session_name)
    if base_name not in existing:
        return base_name
    counter = 2
    while f"{base_name}-{counter}" in existing:
        counter += 1
    return f"{base_name}-{counter}"


def setup_tmux_windows(session_name, command1, command2, command3, command3_filtered, command4, command5):
    try:
        # ── ports window: tcp (top) + udp (bottom) ──
        ports_name = unique_window_name(session_name, 'ports')
        subprocess.run(['tmux', 'new-window', '-a', '-t', session_name, '-n', ports_name], check=True)
        subprocess.run(['tmux', 'split-window', '-v', '-t', f'{session_name}:{ports_name}'], check=True)
        run_zsh_command_in_tmux_pane(session_name, f'{ports_name}.0', command1)
        run_zsh_command_in_tmux_pane(session_name, f'{ports_name}.1', command2)

        # ── vhost window: raw scan (top) + filtered matcher waiting (bottom) ──
        vhost_name = unique_window_name(session_name, 'vhost')
        subprocess.run(['tmux', 'new-window', '-a', '-t', session_name, '-n', vhost_name], check=True)
        subprocess.run(['tmux', 'split-window', '-v', '-t', f'{session_name}:{vhost_name}'], check=True)
        run_zsh_command_in_tmux_pane(session_name, f'{vhost_name}.0', command3)
        subprocess.run(['tmux', 'send-keys', '-t', f'{session_name}:{vhost_name}.1', command3_filtered], check=True)

        # ── fuzz window: feroxbuster (top) + ffuf (bottom) ──
        fuzz_name = unique_window_name(session_name, 'fuzz')
        subprocess.run(['tmux', 'new-window', '-a', '-t', session_name, '-n', fuzz_name], check=True)
        subprocess.run(['tmux', 'split-window', '-v', '-t', f'{session_name}:{fuzz_name}'], check=True)
        run_zsh_command_in_tmux_pane(session_name, f'{fuzz_name}.0', command4)
        run_zsh_command_in_tmux_pane(session_name, f'{fuzz_name}.1', command5)

        subprocess.run(['tmux', 'select-window', '-t', f'{session_name}:{ports_name}'], check=True)

    except subprocess.CalledProcessError as e:
        print(f"[-] Failed to set up tmux windows in session '{session_name}': {e}")
        sys.exit(1)


def start_tmux_session_with_windows(session_name, command1, command2, command3, command3_filtered, command4, command5):
    current_session = get_current_tmux_session()

    if current_session:
        print(f"[+] Already inside tmux session '{current_session}' — adding windows there.")
        setup_tmux_windows(current_session, command1, command2, command3, command3_filtered, command4, command5)
    else:
        print(f"[+] No active tmux session detected — creating new session '{session_name}'.")
        try:
            subprocess.run(['tmux', 'new-session', '-d', '-s', session_name, '-n', 'init'], check=True)
            setup_tmux_windows(session_name, command1, command2, command3, command3_filtered, command4, command5)
            subprocess.run(['tmux', 'kill-window', '-t', f'{session_name}:init'], check=True)
            subprocess.run(['tmux', 'attach-session', '-t', session_name])
        except subprocess.CalledProcessError as e:
            print(f"[-] Failed to create tmux session '{session_name}': {e}")
            sys.exit(1)


def ping_host(ip):
    consecutive_failures = 0
    print("[i] Waiting for host to come up...")
    while True:
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '1', ip],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if result.returncode == 0:
                print(f"\n[+] Got ping!")
                return

            consecutive_failures += 1
            print(f"[i] No response yet (attempt {consecutive_failures})...", end='\r')

        except Exception as e:
            print(f"[-] Error while trying to ping {ip}: {e}")
            sys.exit(1)

        if consecutive_failures % 15 == 0:
            action = input("\n[-] Could not ping host yet! (c)ontinue/(s)kip/(e)xit? ").strip().lower()
            if action == 'c':
                print("[+] Continuing to ping...")
            elif action == 's':
                print("[+] Skipping ping checks and continuing...")
                return
            elif action == 'e':
                print("[+] Exiting the script.")
                sys.exit(0)
            else:
                print("[-] Invalid input, please enter 'c', 's', or 'e'.")
        else:
            time.sleep(1)


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

tun0_ip = check_tun0_interface()
print(f"[+] tun0 is connected with IP: {tun0_ip}")

box_name = input("[?] Enter the name of the box: ").strip()

create_directory(box_name)
os.chdir(f"/home/david/hackTheBox/machines/{box_name}/")

# ── HTB-CLI: try to start machine and get IP automatically ──
box_ip = get_box_ip_via_htb_cli(box_name)

if box_ip:
    print(f"[+] Box IP obtained automatically: {box_ip}")
else:
    print(f"[!] Could not start machine or extract IP via htb-cli.")
    print(f"[!] Please start the machine manually on HTB and enter the details below.")
    box_name = input(f"[?] Confirm box name (or re-enter if different) [{box_name}]: ").strip() or box_name
    box_ip   = input("[?] Enter the box IP manually: ").strip()

print(f"[i] Using box '{box_name}' at IP {box_ip}")
print("[i] Pinging the box to check if it's up...")

ping_host(box_ip)

run_zsh_command(f"addhost {box_ip} {box_name}.htb")
os.system(FULLSCREEN_I3_PANE)

command1          = f"nmap_tcp {box_ip}"
command2          = f"nmap_udp {box_ip}"
command3          = f"sleep 2 && vhost {box_name}.htb"
command3_filtered = f"vhost {box_name}.htb -fw <words> or -fl <lines>"
command4          = f"fuzz_dir http://{box_name}.htb"
command5          = f"/opt/feroxbuster -u http://{box_name}.htb"

start_tmux_session_with_windows(box_name, command1, command2, command3, command3_filtered, command4, command5)