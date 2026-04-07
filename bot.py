#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
import asyncio
import subprocess
import json
from datetime import datetime, timedelta, timezone
import shlex
import logging
import shutil
import os
from typing import Optional, List, Dict, Any
import threading
import time
import sqlite3
import random
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is required!")

BOT_NAME = os.getenv('BOT_NAME', 'FluxNodes')
PREFIX = os.getenv('PREFIX', '!')
YOUR_SERVER_IP = os.getenv('YOUR_SERVER_IP', '127.0.0.1')
MAIN_ADMIN_ID = int(os.getenv('MAIN_ADMIN_ID', '0'))
VPS_USER_ROLE_ID = int(os.getenv('VPS_USER_ROLE_ID', '0'))
DEFAULT_STORAGE_POOL = os.getenv('DEFAULT_STORAGE_POOL', 'default')
BOT_VERSION = os.getenv('BOT_VERSION', '8.0-PRO')
BOT_DEVELOPER = os.getenv('BOT_DEVELOPER', 'WalksysDev')

# Currency & Theme Configuration
CREDIT_MULTIPLIER = 1000
FOOTER_IMAGE_URL = "https://i.ibb.co/tpmJ95jK/08d327f8e6de.png"

# OS Options
OS_OPTIONS = [
    {"label": "Ubuntu 20.04 LTS", "value": "ubuntu:20.04"},
    {"label": "Ubuntu 22.04 LTS", "value": "ubuntu:22.04"},
    {"label": "Ubuntu 24.04 LTS", "value": "ubuntu:24.04"},
    {"label": "Debian 10 (Buster)", "value": "images:debian/10"},
    {"label": "Debian 11 (Bullseye)", "value": "images:debian/11"},
    {"label": "Debian 12 (Bookworm)", "value": "images:debian/12"},
    {"label": "Debian 13 (Trixie)", "value": "images:debian/13"},
]

# ==========================================
# GLOBAL VARIABLES
# ==========================================

# FIX: Define resource_monitor_active globally
resource_monitor_active = True

vps_data = {}
admin_data = {'admins': []}

# ==========================================
# LOGGING CONFIGURATION (FIXED ENCODING)
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'), # FIXED: Encoding added here
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(f'{BOT_NAME.lower()}_vps_bot')

# ==========================================
# DATABASE CONNECTION POOL
# ==========================================

_db_connection = None
_db_lock = threading.Lock()

def get_db():
    """Get a thread-safe database connection"""
    global _db_connection
    with _db_lock:
        if _db_connection is None:
            _db_connection = sqlite3.connect('vps.db', timeout=60.0, check_same_thread=False, isolation_level=None)
            _db_connection.execute("PRAGMA journal_mode=WAL")
            _db_connection.execute("PRAGMA busy_timeout=60000")
            _db_connection.execute("PRAGMA synchronous=NORMAL")
            _db_connection.row_factory = sqlite3.Row
        return _db_connection

async def run_in_executor(func, *args):
    """Run blocking database operations in thread executor"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)

# ==========================================
# INITIALIZATION
# ==========================================

def init_db():
    """Initialize database tables and default data"""
    conn = get_db()
    cur = conn.cursor()
    
    # FIX: Ensure all tables are created with IF NOT EXISTS
    
    # 1. Admins
    cur.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id TEXT PRIMARY KEY
    )''')
    cur.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (str(MAIN_ADMIN_ID),))
    
    # 2. Nodes
    cur.execute('''CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        location TEXT,
        total_vps INTEGER,
        tags TEXT DEFAULT '[]',
        api_key TEXT,
        url TEXT,
        is_local INTEGER DEFAULT 0
    )''')
    cur.execute('SELECT COUNT(*) FROM nodes WHERE is_local = 1')
    if cur.fetchone()[0] == 0:
        cur.execute('INSERT INTO nodes (name, location, total_vps, tags, api_key, url, is_local) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    ('Local Node', 'Local', 100, '[]', None, None, 1))

    # 3. VPS
    cur.execute('''CREATE TABLE IF NOT EXISTS vps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        node_id INTEGER NOT NULL DEFAULT 1,
        container_name TEXT UNIQUE NOT NULL,
        ram TEXT NOT NULL,
        cpu TEXT NOT NULL,
        storage TEXT NOT NULL,
        config TEXT NOT NULL,
        os_version TEXT DEFAULT 'ubuntu:22.04',
        status TEXT DEFAULT 'stopped',
        suspended INTEGER DEFAULT 0,
        whitelisted INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        shared_with TEXT DEFAULT '[]',
        suspension_history TEXT DEFAULT '[]',
        expires_at TEXT,
        duration_days INTEGER DEFAULT 7,
        auto_renew INTEGER DEFAULT 0
    )''')

    # 4. Settings
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )''')
    
    # 5. Port Allocations
    cur.execute('''CREATE TABLE IF NOT EXISTS port_allocations (
        user_id TEXT PRIMARY KEY,
        allocated_ports INTEGER DEFAULT 0
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS port_forwards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        vps_container TEXT NOT NULL,
        vps_port INTEGER NOT NULL,
        host_port INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )''')
    
    # 6. User Coins (Economy)
    cur.execute('''CREATE TABLE IF NOT EXISTS user_coins (
        user_id TEXT PRIMARY KEY,
        balance INTEGER DEFAULT 0,
        total_earned INTEGER DEFAULT 0,
        total_spent INTEGER DEFAULT 0,
        last_daily TEXT,
        invite_count INTEGER DEFAULT 0,
        message_count INTEGER DEFAULT 0,
        voice_minutes INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )''')
    
    # 7. Deploy Plans (CRITICAL FIX - The missing table)
    cur.execute('''CREATE TABLE IF NOT EXISTS deploy_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT,
        ram_gb INTEGER NOT NULL,
        cpu_cores INTEGER NOT NULL,
        disk_gb INTEGER NOT NULL,
        duration_days INTEGER NOT NULL,
        cost_coins INTEGER NOT NULL,
        active INTEGER DEFAULT 1,
        icon TEXT DEFAULT 'ðŸ“¦',
        created_at TEXT NOT NULL
    )''')
    
    # 8. Coin Transactions
    cur.execute('''CREATE TABLE IF NOT EXISTS coin_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        amount INTEGER NOT NULL,
        type TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL
    )''')

    # Default Settings
    settings_init = [
        ('cpu_threshold', '90'),
        ('ram_threshold', '90'),
    ]
    for key, value in settings_init:
        cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))

    # ==========================================
    # RESET & INITIALIZE PLANS
    # ==========================================
    cur.execute('DELETE FROM deploy_plans')
    
    # New Packages (Based on Request)
    new_deploy_plans = [
        ('Starter VPS', 'Perfect for testing and learning', 1, 1, 16, 30, 600000, 'ðŸŒ±'),
        ('Basic VPS', 'Good for small projects', 2, 2, 32, 30, 900000, 'ðŸ“¦'),
        ('Standard VPS', 'Balanced resources for most uses', 4, 4, 64, 30, 1400000, 'âš™ï¸'),
        ('Advanced VPS', 'More power for demanding apps', 6, 6, 128, 30, 2100000, 'ðŸš€'),
        ('Pro VPS', 'Maximum performance', 8, 8, 256, 30, 3200000, 'ðŸ’Ž'),
        ('Business VPS', 'Enterprise grade resources', 10, 10, 512, 30, 4200000, 'ðŸ‘”'),
        ('Ultimate VPS', 'Unmatched power and storage', 12, 12, 1024, 30, 5500000, 'ðŸ‘‘'),
        ('Special Offer A', 'High RAM, Low CPU, Low Disk', 16, 4, 32, 30, 1500000, 'ðŸŽ'),
        ('Special Offer B', 'High RAM, Low CPU, High Disk', 16, 4, 64, 30, 1800000, 'ðŸŽ'),
    ]
    
    for name, desc, ram, cpu, disk, days, cost, icon in new_deploy_plans:
        cur.execute('''INSERT INTO deploy_plans 
                       (name, description, ram_gb, cpu_cores, disk_gb, duration_days, cost_coins, icon, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (name, desc, ram, cpu, disk, days, cost, icon, datetime.now().isoformat()))

    # Economy Settings (Scaled)
    coin_settings = [
        ('coins_per_invite', str(50 * CREDIT_MULTIPLIER)),
        ('coins_per_message', str(1 * CREDIT_MULTIPLIER)),
        ('coins_per_voice_minute', str(2 * CREDIT_MULTIPLIER)),
        ('coins_daily_reward', str(100 * CREDIT_MULTIPLIER)),
        ('coins_vps_renewal_1day', str(50 * CREDIT_MULTIPLIER)),
        ('coins_vps_renewal_7days', str(300 * CREDIT_MULTIPLIER)),
        ('coins_vps_renewal_30days', str(1000 * CREDIT_MULTIPLIER)),
        ('default_vps_duration_days', '7'),
        ('vps_expiry_warning_hours', '24'),
        ('message_cooldown_seconds', '60'),
        ('voice_min_duration_minutes', '5'),
    ]
    for key, value in coin_settings:
        cur.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
        
    conn.commit()
    conn.close()

# ==========================================
# THEME & EMBED SYSTEM (RED)
# ==========================================

class EmbedColors:
    PRIMARY = 0xFF0000
    SUCCESS = 0xFF0000
    ERROR = 0x8B0000
    WARNING = 0xFF0000
    INFO = 0xFF0000
    PREMIUM = 0xFF0000
    GOLD = 0xFF0000
    PURPLE = 0xFF0000
    DARK = 0x2B2D31
    LIGHT = 0x99AAB5

class EmbedIcons:
    SUCCESS = "✓"
    ERROR = "✕"
    WARNING = "⚠"
    INFO = "ℹ"
    LOADING = "⟳"
    PREMIUM = "★"
    ARROW = "→"
    BULLET = "•"
    DIVIDER = "—"

def create_embed(title, description="", color=EmbedColors.PRIMARY, show_branding=True):
    clean_title = title[:256] if title else ""
    if color not in [EmbedColors.DARK, EmbedColors.LIGHT]:
        color = EmbedColors.PRIMARY
    embed = discord.Embed(
        title=clean_title,
        description=description[:4096] if description else None,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    if show_branding:
        embed.set_footer(
            text=f"{BOT_NAME} v{BOT_VERSION} | Powered by {BOT_DEVELOPER}",
            icon_url=FOOTER_IMAGE_URL
        )
    return embed

def add_field(embed, name, value, inline=False):
    if not value: value = "N/A"
    embed.add_field(name=name[:256], value=str(value)[:1024], inline=inline)
    return embed

def create_success_embed(title, description="", show_icon=True):
    icon = f"{EmbedIcons.SUCCESS} " if show_icon else ""
    return create_embed(f"{icon}{title}", description, color=EmbedColors.SUCCESS)

def create_error_embed(title, description="", show_icon=True):
    icon = f"{EmbedIcons.ERROR} " if show_icon else ""
    return create_embed(f"{icon}{title}", description, color=EmbedColors.ERROR)

def create_info_embed(title, description="", show_icon=True):
    icon = f"{EmbedIcons.INFO} " if show_icon else ""
    return create_embed(f"{icon}{title}", description, color=EmbedColors.INFO)

def create_warning_embed(title, description="", show_icon=True):
    icon = f"{EmbedIcons.WARNING} " if show_icon else ""
    return create_embed(f"{icon}{title}", description, color=EmbedColors.WARNING)

# ==========================================
# HELPERS
# ==========================================

def truncate_text(text, max_length=1024):
    if not text: return text
    if len(text) <= max_length: return text
    return text[:max_length-3] + "..."

def format_status_badge(status, online_text="Online", offline_text="Offline"):
    is_online = status.lower() in ['running', 'online', 'active', 'started'] if isinstance(status, str) else status
    indicator = "🟢" if is_online else "🔴"
    text = online_text if is_online else offline_text
    return f"{indicator} **{text}**"

def get_setting(key: str, default: Any = None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

# ==========================================
# DATA LOADERS
# ==========================================

# Initialize DB immediately
init_db()

def get_vps_data() -> Dict[str, List[Dict[str, Any]]]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM vps')
    rows = cur.fetchall()
    conn.close()
    data = {}
    for row in rows:
        user_id = row['user_id']
        if user_id not in data:
            data[user_id] = []
        vps = dict(row)
        vps['shared_with'] = json.loads(vps['shared_with'])
        vps['suspension_history'] = json.loads(vps['suspension_history'])
        vps['suspended'] = bool(vps['suspended'])
        vps['whitelisted'] = bool(vps['whitelisted'])
        data[user_id].append(vps)
    return data

def save_vps_data():
    conn = get_db()
    cur = conn.cursor()
    for user_id, vps_list in vps_data.items():
        for vps in vps_list:
            shared_json = json.dumps(vps['shared_with'])
            history_json = json.dumps(vps['suspension_history'])
            suspended_int = 1 if vps['suspended'] else 0
            whitelisted_int = 1 if vps.get('whitelisted', False) else 0
            
            if 'id' not in vps or vps['id'] is None:
                cur.execute('''INSERT INTO vps (user_id, node_id, container_name, ram, cpu, storage, config, os_version, status, suspended, whitelisted, created_at, shared_with, suspension_history)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                            (user_id, vps.get('node_id', 1), vps['container_name'], vps['ram'], vps['cpu'], vps['storage'], vps['config'],
                             vps.get('os_version', 'ubuntu:22.04'), vps['status'], suspended_int, whitelisted_int,
                             vps.get('created_at', datetime.now().isoformat()), shared_json, history_json))
            else:
                cur.execute('''UPDATE vps SET user_id = ?, node_id = ?, container_name = ?, ram = ?, cpu = ?, storage = ?, config = ?, os_version = ?, status = ?, suspended = ?, whitelisted = ?, shared_with = ?, suspension_history = ?
                               WHERE id = ?''',
                            (user_id, vps.get('node_id', 1), vps['container_name'], vps['ram'], vps['cpu'], vps['storage'], vps['config'],
                             vps.get('os_version', 'ubuntu:22.04'), vps['status'], suspended_int, whitelisted_int, shared_json, history_json, vps['id']))
    conn.commit()
    conn.close()

def get_admins() -> List[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM admins')
    rows = cur.fetchall()
    conn.close()
    return [row['user_id'] for row in rows]

# Load Initial Data
vps_data = get_vps_data()
admin_data = {'admins': get_admins()}

# Global Settings
CPU_THRESHOLD = int(get_setting('cpu_threshold', 90))
RAM_THRESHOLD = int(get_setting('ram_threshold', 90))

# ==========================================
# BOT SETUP (CLIENT NAMING)
# ==========================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.voice_states = True

# FIX: Use 'client' consistently
client = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ==========================================
# ASYNC DB HELPERS
# ==========================================

def get_user_coins(user_id: str) -> Dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM user_coins WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    else:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''INSERT INTO user_coins (user_id, balance, total_earned, total_spent, created_at)
                       VALUES (?, 0, 0, 0, ?)''', (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return {'user_id': user_id, 'balance': 0, 'total_earned': 0, 'total_spent': 0, 'invite_count': 0, 'message_count': 0, 'voice_minutes': 0}

def add_coins(user_id: str, amount: int, transaction_type: str, description: str = None) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''INSERT OR IGNORE INTO user_coins (user_id, balance, total_earned, total_spent, created_at)
                   VALUES (?, 0, 0, 0, ?)''', (user_id, datetime.now().isoformat()))
    cur.execute('''UPDATE user_coins SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id = ?''', (amount, amount, user_id))
    cur.execute('''INSERT INTO coin_transactions (user_id, amount, type, description, created_at) VALUES (?, ?, ?, ?, ?)''',
                (user_id, amount, transaction_type, description, datetime.now().isoformat()))
    cur.execute('SELECT balance FROM user_coins WHERE user_id = ?', (user_id,))
    new_balance = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return new_balance

def remove_coins(user_id: str, amount: int, transaction_type: str, description: str = None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT balance FROM user_coins WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    if not row or row[0] < amount:
        conn.close()
        return False, row[0] if row else 0
    cur.execute('''UPDATE user_coins SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id = ?''', (amount, amount, user_id))
    cur.execute('''INSERT INTO coin_transactions (user_id, amount, type, description, created_at) VALUES (?, ?, ?, ?, ?)''',
                (user_id, -amount, transaction_type, description, datetime.now().isoformat()))
    conn.commit()
    cur.execute('SELECT balance FROM user_coins WHERE user_id = ?', (user_id,))
    new_balance = cur.fetchone()[0]
    conn.close()
    return True, new_balance

# ==========================================
# LXC / NODE MANAGEMENT
# ==========================================

def get_nodes() -> List[Dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM nodes')
    rows = cur.fetchall()
    conn.close()
    nodes = [dict(row) for row in rows]
    for node in nodes:
        node['tags'] = json.loads(node['tags'])
    return nodes

def get_node(node_id: int) -> Optional[Dict]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM nodes WHERE id = ?', (node_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        node = dict(row)
        node['tags'] = json.loads(node['tags'])
        return node
    return None

def get_current_vps_count(node_id: int) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM vps WHERE node_id = ?', (node_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

def find_node_id_for_container(container_name: str) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT node_id FROM vps WHERE container_name = ?', (container_name,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 1

async def execute_lxc(container_name: str, command: str, timeout=120, node_id: Optional[int] = None):
    if node_id is None:
        node_id = find_node_id_for_container(container_name)
    node = get_node(node_id)
    
    if not node:
        raise Exception(f"Node {node_id} not found")
    
    full_command = f"lxc {command}"
    
    if node['is_local']:
        try:
            cmd = shlex.split(full_command)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise asyncio.TimeoutError(f"Command timed out after {timeout} seconds")
            
            if proc.returncode != 0:
                error = stderr.decode().strip() if stderr else "Command failed with no error output"
                raise Exception(f"Local LXC command failed: {error}\nCommand: {full_command}")
            return stdout.decode().strip() if stdout else True
        except asyncio.TimeoutError as te:
            logger.error(f"LXC command timed out: {full_command} - {str(te)}")
            raise
        except Exception as e:
            logger.error(f"LXC Error: {full_command} - {str(e)}")
            raise
    else:
        url = f"{node['url']}/api/execute"
        data = {"command": full_command}
        params = {"api_key": node["api_key"]}
        try:
            response = requests.post(url, json=data, params=params, timeout=timeout)
            
            try:
                error_detail = response.json()
                if 'detail' in error_detail:
                    error_msg = error_detail['detail']
                elif 'error' in error_detail:
                    error_msg = error_detail['error']
                else:
                    error_msg = response.text
            except:
                error_msg = response.text
            
            response.raise_for_status()
            
            res = response.json()
            if res.get("returncode", 1) != 0:
                stderr = res.get("stderr", "Command failed")
                raise Exception(f"Remote LXC command failed on {node['name']}: {stderr}\nCommand: {full_command}")
            return res.get("stdout", True)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Remote LXC error on node {node['name']} ({url}): {str(e)}")
            raise Exception(f"Remote execution failed on {node['name']}: {str(e)}")

async def apply_lxc_config(container_name: str, node_id: int):
    try:
        await execute_lxc(container_name, f"config set {container_name} security.nesting true", node_id=node_id)
        await execute_lxc(container_name, f"config set {container_name} security.privileged true", node_id=node_id)
        await execute_lxc(container_name, f"config set {container_name} security.syscalls.intercept.mknod true", node_id=node_id)
        await execute_lxc(container_name, f"config set {container_name} security.syscalls.intercept.setxattr true", node_id=node_id)
        await execute_lxc(container_name, f"config set {container_name} linux.kernel_modules overlay,loop,nf_nat,ip_tables,ip6_tables,netlink_diag,br_netfilter", node_id=node_id)
        try:
            await execute_lxc(container_name, f"config device add {container_name} fuse unix-char path=/dev/fuse", node_id=node_id)
        except:
            pass
        raw_lxc_config = (
            "lxc.apparmor.profile = unconfined\n"
            "lxc.apparmor.allow_nesting = 1\n"
            "lxc.apparmor.allow_incomplete = 1\n"
            "\n"
            "lxc.cap.drop =\n"
            "lxc.cgroup.devices.allow = a\n"
            "lxc.cgroup2.devices.allow = a\n"
            "\n"
            "lxc.mount.auto = proc:rw sys:rw cgroup:rw shmounts:rw\n"
            "\n"
            "lxc.mount.entry = /dev/fuse dev/fuse none bind,create=file 0 0\n"
        )
        await execute_lxc(container_name, f"config set {container_name} raw.lxc '{raw_lxc_config}'", node_id=node_id)
        logger.info(f"LXC permissions applied to {container_name}")
    except Exception as e:
        logger.error(f"Failed to apply LXC config to {container_name}: {e}")

async def apply_internal_permissions(container_name: str, node_id: int):
    try:
        await asyncio.sleep(5)
        commands = [
            "mkdir -p /etc/sysctl.d/",
            "echo 'net.ipv4.ip_unprivileged_port_start=0' > /etc/sysctl.d/99-custom.conf",
            "echo 'net.ipv4.ping_group_range=0 2147483647' >> /etc/sysctl.d/99-custom.conf",
            "sysctl -p /etc/sysctl.d/99-custom.conf || true"
        ]
        for cmd in commands:
            try:
                await execute_lxc(container_name, f"exec {container_name} -- bash -c \"{cmd}\"", node_id=node_id)
            except Exception as cmd_error:
                logger.warning(f"Command failed in {container_name}: {cmd} - {cmd_error}")
        logger.info(f"Internal permissions applied to {container_name}")
    except Exception as e:
        logger.error(f"Failed to apply internal permissions to {container_name}: {e}")

async def get_or_create_vps_role(guild):
    global VPS_USER_ROLE_ID
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return None
    role_name = f"{BOT_NAME} VPS User"
    if VPS_USER_ROLE_ID:
        role = guild.get_role(VPS_USER_ROLE_ID)
        if role and role < me.top_role:
            return role
        VPS_USER_ROLE_ID = None
    role = discord.utils.get(guild.roles, name=role_name)
    if role:
        if role >= me.top_role:
            try:
                await role.delete(reason="Role above bot, recreating")
            except discord.Forbidden:
                return None
            role = None
        else:
            VPS_USER_ROLE_ID = role.id
            return role
    try:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color.red(),
            permissions=discord.Permissions.none(),
            reason=f"{BOT_NAME} VPS User role"
        )
        await role.edit(position=me.top_role.position - 1)
        VPS_USER_ROLE_ID = role.id
        logger.info(f"Created VPS role: {role.id}")
        return role
    except Exception as e:
        logger.error(f"Failed to create VPS role: {e}")
        return None

# ==========================================
# BOT EVENTS
# ==========================================

@client.event
async def on_ready():
    logger.info(f'{client.user} has connected to Discord!')
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{BOT_NAME} VPS Manager"))
    logger.info(f"{BOT_NAME} Bot is ready!")
    client.loop.create_task(expiration_checker_loop())

@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=create_error_embed("Missing Argument", "Please check command usage with `!help`."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=create_error_embed("Invalid Argument", "Please check your input and try again."))
    elif isinstance(error, commands.CheckFailure):
        error_msg = str(error) if str(error) else "You need admin permissions."
        await ctx.send(embed=create_error_embed("Access Denied", error_msg))
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(embed=create_error_embed("System Error", "An unexpected error occurred."))

# ==========================================
# TASKS & LOOPS
# ==========================================

async def expiration_checker_loop():
    await client.wait_until_ready()
    warned_24h = set()
    warned_12h = set()
    warned_1h = set()
    
    while not client.is_closed():
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute('''SELECT id, user_id, container_name, expires_at, whitelisted
                           FROM vps 
                           WHERE expires_at IS NOT NULL 
                           AND suspended = 0
                           AND whitelisted = 0''')
            all_vps = cur.fetchall()
            conn.close()
            
            for vps in all_vps:
                vps_id, user_id, container_name, expires_at_str, whitelisted = vps
                
                if not expires_at_str or whitelisted:
                    continue
                
                expires_at = datetime.fromisoformat(expires_at_str)
                hours_left = (expires_at - datetime.now()).total_seconds() / 3600
                
                if hours_left <= 0:
                    continue
                
                try:
                    user = await client.fetch_user(int(user_id))
                    renewal_cost_1day = int(get_setting('coins_vps_renewal_1day', 50 * CREDIT_MULTIPLIER))
                    
                    if 23 <= hours_left <= 25 and vps_id not in warned_24h:
                        embed = create_warning_embed("⚠️ VPS Expiring in 24 Hours!", 
                            f"Your VPS `{container_name}` will expire in **24 hours**!\n\n"
                            f"**Renewal:**\n"
                            f"• 1 Day: {renewal_cost_1day:,} Credits")
                        await user.send(embed=embed)
                        warned_24h.add(vps_id)
                    elif 0.5 <= hours_left <= 1.5 and vps_id not in warned_1h:
                        embed = create_error_embed("⏰ VPS EXPIRING IN 1 HOUR!", 
                            f"Your VPS `{container_name}` will expire in **1 hour**!\n\n"
                            f"**Quick Renewal:**\n"
                            f"• 1 Day: {renewal_cost_1day:,} Credits")
                        await user.send(embed=embed)
                        warned_1h.add(vps_id)
                except Exception:
                    pass
            
            current_vps_ids = {vps[0] for vps in all_vps}
            warned_24h = warned_24h & current_vps_ids
            warned_1h = warned_1h & current_vps_ids
            
        except Exception as e:
            logger.error(f"Error in expiration checker: {e}")
        
        await asyncio.sleep(600)

# ==========================================
# COMMANDS
# ==========================================

def is_admin():
    async def predicate(ctx):
        if str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", []):
            return True
        raise commands.CheckFailure("You need admin permissions.")
    return commands.check(predicate)

@client.command(name='ping')
async def ping(ctx):
    latency = round(client.latency * 1000)
    embed = create_embed("Connection Status", color=EmbedColors.PRIMARY)
    add_field(embed, "Latency", f"**{latency}ms**", True)
    add_field(embed, "Status", format_status_badge(True), True)
    await ctx.send(embed=embed)

@client.command(name='balance', aliases=['bal', 'coins', 'wallet'])
async def balance(ctx, user: discord.Member = None):
    target_user = user or ctx.author
    user_id = str(target_user.id)
    
    coins_data = await run_in_executor(get_user_coins, user_id)
    display_balance = coins_data['balance'] 
    
    embed = create_card_embed("Credit Wallet", f"Financial overview for {target_user.mention}", color=EmbedColors.GOLD)
    balance_display = f"```\n{display_balance:,} Credits\n```"
    add_field(embed, "💰 Current Balance", balance_display, False)
    add_field(embed, "₮ Total Earned", f"```{coins_data['total_earned']:,}```", True)
    add_field(embed, "Quick Actions", f"`{PREFIX}daily` | `{PREFIX}deploy-plans`", False)
    await ctx.send(embed=embed)

@client.command(name='deploy-plans', aliases=['plans', 'vps-plans'])
async def show_deploy_plans(ctx):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM deploy_plans WHERE active = 1 ORDER BY cost_coins')
    plans = [dict(row) for row in cur.fetchall()]
    conn.close()
    
    if not plans:
        await ctx.send(embed=create_error_embed("No Plans Available", "Contact an admin."))
        return
    
    embed = create_info_embed("🚀 VPS Deployment Plans", "Choose a plan and deploy your VPS!")
    
    for plan in plans:
        plan_info = (
            f"**Resources:** {plan['ram_gb']}GB RAM, {plan['cpu_cores']} CPU, {plan['disk_gb']}GB Disk\n"
            f"**Cost:** {plan['cost_coins']:,} Credits\n"
            f"**Deploy:** `{PREFIX}deploy {plan['id']}`"
        )
        add_field(embed, f"{plan['icon']} {plan['name']}", plan_info, True)
    
    await ctx.send(embed=embed)

@client.command(name='deploy', aliases=['buy-vps'])
async def deploy_vps(ctx, plan_id: int = None):
    user_id = str(ctx.author.id)
    
    if len(vps_data.get(user_id, [])) >= 1:
        await ctx.send(embed=create_error_embed("VPS Limit Reached", "You already have a VPS."))
        return
        
    if not plan_id:
        await ctx.send(embed=create_info_embed("Select a Plan", f"Use `{PREFIX}deploy-plans` to see plans."))
        return
        
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM deploy_plans WHERE id = ?', (plan_id,))
    plan = cur.fetchone()
    conn.close()
    
    if not plan:
        await ctx.send(embed=create_error_embed("Invalid Plan", "Plan not found."))
        return
    
    plan = dict(plan)
    cost = plan['cost_coins']
    
    def check_bal():
        d = get_user_coins(user_id)
        return d['balance']
    balance = await run_in_executor(check_bal)
    
    if balance < cost:
        await ctx.send(embed=create_error_embed("Insufficient Credits", f"You need {cost:,} Credits."))
        return
        
    # Confirmation
    embed = create_info_embed("Confirm Deployment", 
        f"Plan: {plan['name']}\nCost: {cost:,} Credits\nReact with ✅ to confirm.")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id
    
    try:
        reaction, user = await client.wait_for('reaction_add', timeout=60.0, check=check)
        if str(reaction.emoji) == "❌":
            await msg.edit(embed=create_info_embed("Cancelled", "Deployment cancelled."))
            return
            
        # Payment
        def pay():
            s, nb = remove_coins(user_id, cost, 'deploy', 'VPS Purchase')
            return s, nb
        success, new_bal = await run_in_executor(pay)
        
        if not success:
            await msg.edit(embed=create_error_embed("Payment Failed", "Try again."))
            return
            
        await msg.edit(embed=create_info_embed("Deploying...", "Creating VPS..."))
        
        # Node Selection Logic
        nodes = get_nodes()
        selected_node = None
        for node in nodes:
            if get_current_vps_count(node['id']) < node['total_vps']:
                selected_node = node
                break
        
        if not selected_node:
            add_coins(user_id, cost, 'refund', 'No nodes')
            await msg.edit(embed=create_error_embed("Failed", "No nodes available. Refunded."))
            return
            
        node_id = selected_node['id']
        container_name = f"{BOT_NAME.lower()}-vps-{user_id}-1"
        os_version = "ubuntu:22.04"
        
        try:
            # LXC Creation
            await execute_lxc(container_name, f"init {os_version} {container_name} -s {DEFAULT_STORAGE_POOL}", node_id=node_id)
            await execute_lxc(container_name, f"config set {container_name} limits.memory {plan['ram_gb']*1024}MB", node_id=node_id)
            await execute_lxc(container_name, f"config set {container_name} limits.cpu {plan['cpu_cores']}", node_id=node_id)
            await execute_lxc(container_name, f"config device set {container_name} root size={plan['disk_gb']}GB", node_id=node_id)
            await apply_lxc_config(container_name, node_id)
            await execute_lxc(container_name, f"start {container_name}", node_id=node_id)
            await apply_internal_permissions(container_name, node_id)
            
            # Save to DB
            vps_info = {
                "container_name": container_name,
                "node_id": node_id,
                "ram": f"{plan['ram_gb']}GB",
                "cpu": str(plan['cpu_cores']),
                "storage": f"{plan['disk_gb']}GB",
                "config": f"{plan['ram_gb']}GB RAM / {plan['cpu_cores']} CPU / {plan['disk_gb']}GB",
                "os_version": os_version,
                "status": "running",
                "suspended": False,
                "whitelisted": False,
                "suspension_history": [],
                "created_at": datetime.now().isoformat(),
                "shared_with": [],
                "expires_at": (datetime.now() + timedelta(days=plan['duration_days'])).isoformat(),
                "duration_days": plan['duration_days'],
                "auto_renew": 0,
                "id": None
            }
            
            if user_id not in vps_data: vps_data[user_id] = []
            vps_data[user_id].append(vps_info)
            save_vps_data()
            
            # Role
            if ctx.guild:
                r = await get_or_create_vps_role(ctx.guild)
                if r: await ctx.author.add_roles(r)
                
            success_embed = create_success_embed("VPS Deployed", f"VPS `{container_name}` created successfully!\nNew Balance: {new_bal:,} Credits")
            await msg.edit(embed=success_embed)
            
        except Exception as e:
            add_coins(user_id, cost, 'refund', str(e))
            await msg.edit(embed=create_error_embed("Deployment Failed", f"{str(e)}\nRefunded."))
            
    except asyncio.TimeoutError:
        await msg.edit(embed=create_info_embed("Timeout", "Request timed out."))

@client.command(name='myvps')
async def my_vps(ctx):
    user_id = str(ctx.author.id)
    vps_list = vps_data.get(user_id, [])

    if not vps_list:
        await ctx.send(embed=create_error_embed("No VPS Found", "You don't have a VPS yet."))
        return

    embed = create_info_embed("My VPS Dashboard", "Your personal VPS overview")
    vps_cards = []

    for i, vps in enumerate(vps_list, start=1):
        node = get_node(vps.get("node_id"))
        node_name = node["name"] if node else "Unknown"
        status = "⛔ SUSPENDED" if vps.get("suspended") else "🟢 RUNNING" if vps.get("status") == "running" else "🔴 STOPPED"
        
        vps_cards.append(
            f"**{i}.** `{vps['container_name']}`\n"
            f"{status} • `{vps['config']}`\n"
            f"📍 Node: `{node_name}`"
        )

    vps_text = "\n\n".join(vps_cards)
    for i in range(0, len(vps_text), 1024):
        add_field(embed, "Your VPS", vps_text[i:i + 1024], False)

    await ctx.send(embed=embed)

# Add other necessary commands here following the same pattern...
# (For brevity in this response, I am ending the code block here. 
# Ensure you have the rest of your commands like 'renew', 'manage', etc., following the pattern:
# - Use @client.event
# - Use client.run()
# - Use create_embed functions)

if __name__ == "__main__":
    if DISCORD_TOKEN:
        client.run(DISCORD_TOKEN)
    else:
        logger.error("No Discord token found.")
