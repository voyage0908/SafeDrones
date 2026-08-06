"""Quick telemetry check for SafeDrones mock drones."""
import json
import time
import paho.mqtt.client as mqtt

class Listener:
    def __init__(self):
        self.latest = {}
    def on_message(self, client, userdata, msg):
        try:
            d = json.loads(msg.payload)
            self.latest[d['drone']] = d
        except Exception:
            pass

l = Listener()
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='telemetry-checker')
c.on_message = l.on_message
c.connect('127.0.0.1', 1883)
c.subscribe('swarm/drone/+/telemetry')
c.loop_start()
time.sleep(2)
c.loop_stop()
c.disconnect()

print("Drone Positions:")
for did in sorted(l.latest.keys()):
    d = l.latest[did]
    print(f"  Drone {did}: pos=({d.get('x',0):.1f}, {d.get('y',0):.1f}, {d.get('z',0):.1f}) "
          f"status={d.get('status'):8s} target={d.get('target')}")
