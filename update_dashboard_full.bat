@echo off
rem Full DHIS2 re-pull (every period from the start of history, not just the last 3 months), then publish.
rem Use when old months don't match DHIS2 (late edits). Takes over an hour.
cd /d "%~dp0"
call update_dashboard.bat --full
