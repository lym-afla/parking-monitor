# Parking Monitor - Service Issues Fixed

## Problem Summary

The parking monitor services were failing when run as systemd services, despite working correctly when run manually. The issue was caused by overly restrictive systemd security settings that prevented Playwright/Chromium from accessing required system resources.

## Root Causes Identified

### 1. Multiple Process Instances
- Multiple instances of both monitor.py and telegram_bot.py were running simultaneously
- Manual processes (run by user1) were conflicting with systemd services
- This caused state file conflicts and resource contention

### 2. Log File Permission Issues
- Log files were owned by root instead of parking_user
- Service couldn't write logs even though it was running

### 3. Systemd Security Settings Too Restrictive
-  prevented Chromium from accessing /usr and system libraries
-  blocked access to home directories
-  prevented access to devices needed by the browser
- These settings blocked Chromium from launching, even though the executable existed

## Solutions Applied

### 1. Killed Manual Processes


### 2. Fixed Log Permissions


### 3. Relaxed Systemd Security Settings
Modified :

**Before (Restrictive):**


**After (Balanced):**


The key changes:
- Removed  - allows Chromium to access system libraries
- Removed  - allows access to home directories
- Removed  - allows browser to access required devices
- Kept  and  for basic security

### 4. Added Logging to monitor.py
Added comprehensive logging throughout the monitor script to track:
- Service startup
- Browser launch
- Website navigation steps
- Check results
- Errors with full tracebacks

## Verification

After fixes, both services are running correctly:



State file updates correctly:


## Key Lessons

1. **Playwright/Chromium + Systemd Security**: Browser automation requires access to system resources. Strict systemd sandboxing prevents browsers from launching.

2. **Manual Testing vs Service Testing**: Always test as the service user AND under systemd constraints. Manual tests as the service user may succeed while systemd services fail.

3. **Process Management**: Ensure only one instance of each service runs at a time. Multiple instances cause state conflicts.

4. **Logging is Essential**: Without proper logging, debugging service issues is nearly impossible.

## Recommended Service Configuration

For Playwright/browser automation services, use minimal security restrictions:



## Monitoring Commands



## Date Fixed
2025-12-17

## Status
✅ RESOLVED - Both services running successfully as systemd services
