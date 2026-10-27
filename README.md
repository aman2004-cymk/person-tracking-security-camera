# Person-Tracking Security Camera

This repository contains the real-camera application and ESP32 firmware for a
supervised pan/tilt security-camera prototype. A computer runs person
detection/tracking and optional local face matching; an ESP32 receives bounded
pan/tilt and indicator commands over USB serial and enforces a command watchdog
and hardware E-stop.

The application can automatically follow one visible person. When several
people are visible, it stops automatic target selection and requires the
operator to choose who to follow. Recognition is advisory: an identity match
or alert is not proof of identity and must not be the sole basis for denying
access or taking other consequential action.

This is a supervised prototype, not a certified or unattended security system.
Use clear notice, consented enrollment data, restricted access to evidence, and
a defined deletion period. Provide a manual alternative to face recognition.

## What is and is not included

The primary runtime is the real camera + computer vision + USB serial + ESP32
path. It is not a cloud/IoT service: the current implementation does not
provide Wi-Fi, MQTT, remote dashboards, mobile notifications, or cloud storage.
Security alerts and evidence are local to the computer.

Software simulators, synthetic replay footage, and the Wokwi twin are local
development references and are intentionally excluded from the production
GitHub package. The simulator helped define expected behavior, but it does not
replace testing on the target camera or physical hardware.

## Requirements

- Windows 10/11 computer with Python 3.10+ and a webcam
- ESP32 development board connected over USB for motor/indicator control
- Pan and tilt servos, separate regulated servo supply, LEDs with current
  limiting resistors, and a normally-closed E-stop circuit
- A locally available `yolo11n.pt` Ultralytics model file

Do not power servos from the ESP32 or computer USB supply. Follow the
commissioning sequence in [HARDWARE_BUILD.md](HARDWARE_BUILD.md).

## Install

In PowerShell, from the project directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The base install uses `opencv-python` for camera and person tracking. The
optional local LBPH face matcher requires `opencv-contrib-python`; these
OpenCV distributions must not be installed together because both provide the
same `cv2` module. To enable face matching, replace regular OpenCV:

```powershell
python -m pip uninstall -y opencv-python
python -m pip install opencv-contrib-python
```

On first startup, Ultralytics can download the official `yolo11n.pt` model
asset to its configured weights cache. Allow network access for this initial
download. Alternatively, place an official `yolo11n.pt` file beside the
scripts; the app prefers that local file. Model weights, face samples, trained
face model, attendance records, alerts, and incident evidence are local
runtime data and should not be committed.

For the authorization list, copy the example and edit it locally:

```powershell
Copy-Item authorized_people.example.csv authorized_people.csv
```

Replace the example row with consented, enrolled people who are explicitly
approved for the monitored area. The local `authorized_people.csv` is ignored
by Git to avoid publishing personal names.

## Prepare the ESP32

1. Install Arduino IDE 2.x.
2. In **File > Preferences**, add Espressif's Arduino-ESP32 Boards Manager URL:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
3. Open **Tools > Board > Boards Manager**, search for `esp32`, and install
   **esp32 by Espressif Systems**.
4. Open
   [firmware/esp32_pan_tilt_controller.ino](firmware/esp32_pan_tilt_controller.ino).
   Install `ESP32Servo` using **Sketch > Include Library > Manage Libraries**.
5. Connect the ESP32 by USB, select the matching board (typically **ESP32 Dev
   Module**) and COM port in **Tools**, then upload. Open Serial Monitor at
   **115200 baud** and verify `SAFE_STOP,STARTUP`. Close the monitor before
   starting the Python app because the serial port can only be opened by one
   program at a time.

## Run with physical hardware

1. Build and verify the E-stop and power wiring from
   [HARDWARE_BUILD.md](HARDWARE_BUILD.md).
2. Identify the serial port in Device Manager (for example `COM3`).
3. Start with a webcam source and the real hardware backend:

```powershell
python multi_person_surveillance.py --source 0 --hardware-backend real --esp32-port COM3
```

`--source` may be a webcam index, local video path, or OpenCV-supported stream.
The default hardware backend is `real`; an ESP32 port is required. The optional
simulated backend is a local development reference and may not be present in
the production GitHub checkout.

## Verify the software install

Run the non-hardware unit checks before connecting servos:

```powershell
python -m unittest discover -s tests -v
```

For a camera-only smoke test before the ESP32 is available, the development
checkout can use `--hardware-backend simulated`. This requires the excluded
local development file `simulated_hardware_bridge.py`; it is not part of the
production GitHub package. The real production run command above is the
supported deployment path.

## Local event dashboard

The local dashboard displays saved alert and operator-incident records and
their evidence images. Start it in a second PowerShell window:

```powershell
python dashboard.py
```

Open `http://127.0.0.1:8765/`. The dashboard binds only to loopback and is not
available to other computers. It is an event-review page, not a live camera
control panel; the OpenCV window remains the live operator interface. Do not
expose this development dashboard to a network or the public internet.

### Controls

- One visible person: the camera automatically follows that track.
- Multiple visible people: press `[` or `]` to select; following is not
  automatically assigned in an ambiguous scene.
- `F`: start/stop following the selected person.
- `0`: stop following and clear selection.
- `U`: select and follow the first unregistered track.
- `I`: save an operator-confirmed incident record for the selected visible
  track. This records a still image and timestamped CSV row locally; it does
  not send an external notification.
- `Q` or `Esc`: quit and stop the hardware output.

Automatic alerts for unregistered or unauthorized classifications are
generated after repeated detections. Those classifications are indicators for
operator review, not confirmed identity or authorization decisions.

## Optional consent-based face enrollment

If face matching is enabled, enroll only people who have been informed and
consented. Keep an alternative manual or badge-based verification path.

```powershell
python capture_attendance_data.py --name "Person Name"
python face_attendance.py --retrain
```

The multi-person application uses the locally trained face model and
`authorized_people.csv`. Add only explicitly approved enrolled names to that
file. Secure and periodically delete enrollment samples, models, and records
according to the site's policy.

## Hardware and firmware

- [HARDWARE_BUILD.md](HARDWARE_BUILD.md): wiring and safe bench commissioning
- [firmware/esp32_pan_tilt_controller.ino](firmware/esp32_pan_tilt_controller.ino):
  physical ESP32 serial protocol, angle limits, E-stop, watchdog, and LEDs
- [hardware_protocol.py](hardware_protocol.py) and
  [hardware_bridge.py](hardware_bridge.py): PC-side bounded command protocol
  and serial heartbeat

The PC sends a heartbeat independently of vision inference and requires a
matching ESP32 `STATUS` acknowledgement for every motion command. Missing,
invalid, or out-of-sequence acknowledgements stop the application; on missed
commands, the ESP32 watchdog detaches servos. Test E-stop, target loss, serial
disconnection, and angle limits with unloaded servos before installing the
camera or operating near people.

## Repository scope

Development-only simulation sources, Wokwi files, generated test footage,
biometric data, and runtime evidence are excluded using `.gitignore`. Keep
simulation as a local reference; it is not the deployment path and is not a
substitute for the physical commissioning tests above.
