@echo off
chcp 65001 >nul
title Akmaljon bot - serverga joylash
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1"
