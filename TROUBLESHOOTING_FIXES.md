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
- Service could not write logs even though it was running

### 3. Systemd Security Settings Too Restrictive
- ProtectSystem=strict prevented Chromium from accessing /usr and system libraries
- ProtectHome=true blocked access to home directories
- PrivateDevices=true prevented access to devices needed by the browser
- These settings blocked Chromium from launching, even though the executable existed

## Solutions Applied

### 1. Killed Manual Processes
Identified and killed all manually-run Python processes

### 2. Fixed Log Permissions
sudo chown parking_user:parking_user /opt/parking_monitor/logs/*.log

### 3. Relaxed Systemd Security Settings
Modified /etc/systemd/system/parking-service-monitor.service

The key changes:
- Removed ProtectSystem=strict - allows Chromium to access system libraries
- Removed ProtectHome=true - allows access to home directories
- Removed PrivateDevices=true - allows browser to access required devices
- Kept NoNewPrivileges and PrivateTmp for basic security

### 4. Added Logging to monitor.py
Added comprehensive logging throughout the monitor script to track service startup, browser launch, website navigation steps, check results, and errors with full tracebacks.

## Date Fixed
2025-12-17

## Status
RESOLVED - Both services running successfully as systemd services
