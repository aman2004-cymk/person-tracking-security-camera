# Phase 2 Hardware Build Guide

This guide maps the software interfaces to a low-voltage physical prototype. It
is intended for a supervised bench build, not an unattended deployment.

## Required hardware

| Item | Purpose | Minimum requirement |
|---|---|---|
| ESP32 development board | Motor/indicator safety controller | USB serial capable |
| Pan–tilt bracket | Camera mount | Mechanical stops or travel safely within servo range |
| 2 × positional servos | Pan and tilt movement | Torque sufficient for the camera and bracket |
| 5 V regulated motor supply | Servo power | Sized for both servos' combined stall current with margin |
| USB webcam | Vision input | Connected to the computer running Python |
| 2 × LEDs + resistors | Lock and alert indication | Green lock, red alert; each with a 220–330 Ω resistor |
| Normally-closed emergency-stop switch | ESP32 E-stop input | Opens GPIO 27's ground circuit when pressed |
| Inline fuse and connectors | Low-voltage power protection | Rated for the power supply and servo load |

**Important E-stop boundary:** the firmware detects an open E-stop input and
detaches servo PWM, but this GPIO input does not itself disconnect servo power.
If the installation requires immediate removal of servo power, use an
appropriately rated physical switch or safety-rated power disconnect in the
servo supply path. Do not rely on software alone as a personnel safety system.

## Wiring map

The pin assignments match `firmware/esp32_pan_tilt_controller.ino`.

| ESP32 pin | Connect to | Notes |
|---|---|---|
| GPIO 18 | Pan-servo signal | Servo signal only |
| GPIO 19 | Tilt-servo signal | Servo signal only |
| GPIO 25 | Green LED through resistor | LED cathode to GND |
| GPIO 26 | Red LED through resistor | LED cathode to GND |
| GPIO 27 | E-stop circuit | Use `INPUT_PULLUP`; normally-closed switch connects this pin to GND during normal operation |
| GND | Servo power-supply GND and LED cathodes | All low-voltage grounds must share one reference |

Do **not** power servos from the ESP32 5 V/USB pin. Power them from the separate regulated 5 V supply. Connect only the servo signal wires to GPIO 18/19 and join the power-supply GND to ESP32 GND.

## Safe commissioning order

1. Assemble the mount with motors disconnected from the bracket; verify it moves freely by hand.
2. Upload the ESP32 firmware with servos unplugged. Confirm the board prints `SAFE_STOP,STARTUP` on serial.
3. Test the emergency stop: with the normally-closed circuit opened, the
   firmware prints `SAFE_STOP,ESTOP`, detaches both servos, and turns both
   LEDs off. While E-stop is active the firmware stops processing serial input;
   it does not promise a response for each incoming command. Close the circuit
   again and verify motion remains stopped until a fresh valid command arrives.
4. Connect LEDs only. Send `LED,0,1` and confirm the red LED lights without motor motion; send `LED,0,0` to clear it.
5. Connect one servo with no camera load. Command small angles first and confirm its real direction matches pan/tilt signs in software.
6. Connect the second servo, then fit the empty bracket. Set mechanical travel so commanded limits cannot strain cables or joints.
7. Fit the camera. Measure current draw; verify the power supply, fuse, wires, and connectors remain cool.
8. Run the live application with no person selected. Motors must remain detached/stopped.
9. Select and follow a test subject. Verify target loss, serial unplug, and E-stop each stop motor output.

## Serial smoke test

Use any serial monitor at 115200 baud before starting the Python app:

```text
CMD,1,10.00,0.00,0,0
LED,0,1
STOP,2
```

Expected behavior: the first command moves only within configured limits; the
second changes only the red alert LED; the third detaches motor output without
commanding a return to centre. The bridge and firmware use 115200 baud. The
firmware watchdog timeout is 500 ms.

## First power-on acceptance

Keep the camera and bracket detached until every check below passes. Use small
angle commands and have a person at the physical power disconnect.

1. With servos unplugged, verify startup reports `SAFE_STOP,STARTUP`.
2. With the E-stop circuit open, send a `CMD` from a serial monitor. Confirm
   outputs remain stopped and the LEDs are off. Close the circuit and verify a
   fresh command is needed to move.
3. Send a command beyond each software angle boundary. Verify firmware status
   reports pan clamped to -90..90 degrees and tilt clamped to -30..60 degrees.
4. Send `LED,0,1` and verify only the red alert LED changes; send `LED,0,0`.
5. Send `STOP,2` and verify the servos detach without a center/reposition
   command.
6. Send a valid `CMD`, then send nothing for at least 0.6 seconds. Verify
   `SAFE_STOP,WATCHDOG`, servo detachment, and both LEDs off.
7. Connect and test one unloaded servo at a time. Stop if the direction, range,
   current draw, or temperature is unexpected.
8. Only after these pass, attach the empty mount and then the camera. Repeat
   target-loss, E-stop, and USB-disconnection checks before supervised use.

## Evidence and operating boundary

The computer owns detection, identity classification, operator selection, and
evidence files. The ESP32 owns servo PWM, angle clamping, indicator outputs,
E-stop input handling, and command-watchdog behavior. A face match or alert is
not proof of identity or an automatic enforcement decision: the operator
reviews it before taking action.
