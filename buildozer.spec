[app]
title = OI Desk
package.name = oidesk
package.domain = org.evpulse
source.dir = .
source.include_exts = py
version = 1.0
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,requests,urllib3,idna,certifi,charset-normalizer,websocket-client,pyotp,plyer,openssl,kotakneoapi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,WAKE_LOCK,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
