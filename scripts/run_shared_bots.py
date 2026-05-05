#!/usr/bin/env python3
import sys
import time
import threading
import requests
from datetime import datetime
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("==================================================")
print("   STARTING KALSHI BTC A/B/C SHARED-DATA RUNNER")
print("==================================================")

# 1. Patch requests to cache identical requests for 10 seconds.
# This prevents 3x API hits while requiring ZERO changes to the bot code!
class RequestCache:
    def __init__(self, ttl=10):
        self.ttl = ttl
        self.cache = {}
        self.lock = threading.Lock()
        
    def get(self, url, session=None, params=None, **kwargs):
        # build key
        key = url
        if params:
            p = dict(params)
            if 'candles' in url:
                p.pop('start', None)
                p.pop('end', None)
            key += str(sorted(p.items()))
            
        with self.lock:
            if key in self.cache:
                resp, ts = self.cache[key]
                if time.time() - ts < self.ttl:
                    return resp
                    
            # actual network call while holding lock! (Prevents 3x concurrent requests)
            try:
                if session:
                    resp = _orig_session_get(session, url, params=params, **kwargs)
                else:
                    resp = _orig_get(url, params=params, **kwargs)
                
                if resp.status_code == 200:
                    self.cache[key] = (resp, time.time())
                return resp
            except Exception as e:
                raise e

_orig_get = requests.get
_orig_session_get = requests.Session.get

shared_cache = RequestCache(ttl=10)

def patched_get(*args, **kwargs):
    return shared_cache.get(args[0], **kwargs)
    
def patched_session_get(self, *args, **kwargs):
    return shared_cache.get(args[0], session=self, **kwargs)

requests.get = patched_get
requests.Session.get = patched_session_get

print("[+] Network cache enabled. All 3 bots will transparently share data.")

# 2. Deepcopy CFG config in each script so they don't overwrite each other!
import copy
import config.btc_1hr_config as config_module
_ORIGINAL_CFG = copy.deepcopy(config_module.CFG)

config_module.CFG = copy.deepcopy(_ORIGINAL_CFG)
import scripts.btc_1hr_paper as b1

config_module.CFG = copy.deepcopy(_ORIGINAL_CFG)
import scripts.btc_1hr_hardened as b2

config_module.CFG = copy.deepcopy(_ORIGINAL_CFG)
import scripts.btc_1hr_neohardened as b3

def run_bot(bot, name):
    print(f"[*] Started {name} Thread.")
    bot.main()

t1 = threading.Thread(target=run_bot, args=(b1, "Base Paper Bot"), daemon=True)
t2 = threading.Thread(target=run_bot, args=(b2, "Hardened Bot"), daemon=True)
t3 = threading.Thread(target=run_bot, args=(b3, "Neo-Hardened Bot"), daemon=True)

t1.start()
time.sleep(1)
t2.start()
time.sleep(1)
t3.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n[-] Stopped by user.")
