"""Raylogic MOD2U - 2 channel WiFi smart switch (relay) integration.

Aapke H81 (dimmer) integration jaisa hi multi-device architecture, lekin
MOD2U ke apne command bytes ke saath (Docklight se confirmed):
    - Device ID  = 101   (H81 = 002)
    - Area byte  = 0C    (H81 = 02)
    - ON level   = 02, OFF level = 01   (H81 dimmer ka 01=ON/FF=OFF ULTA hai)
    - 2 channels (relay), dimmer nahi - isliye `switch` platform (light nahi)

configuration.yaml mein 'devices' list ke through jitne chaho utne MOD2U
switches add kar sakte ho. Details README.md mein.
"""

DOMAIN = "raylogic_mod2u"
