# Changelog

## 1.5.0 — 2026-11-24

- The Viewer can record displayed camera video to a local MP4 file. Stopping, disconnecting or closing the Viewer finalizes the file.

## 1.4.0 — 2026-11-24

- The Viewer saves the ten most recent successful IP and port combinations. Choose a saved connection to fill in the address and port fields.
- The first history list includes cameras with previously saved identities.
- `Compartir.cmd`, `Ver.cmd`, and `Setup.cmd` are grouped in the `Launchers` folder.

## 1.3.1 — 2026-11-24

- The PIN protects commands from the Windows tray. The launchers and window controls work directly.

## 1.3.0 — 2026-11-24

- Click **Connect** to view a camera by IP address and port once the host enables LAN sharing. A viewer can connect while the host is minimized to the tray.
- When a viewer disconnects, the host keeps sharing enabled and can accept the next connection.
- A local PIN protects app access and commands, including tray actions. The initial PIN is `0000` and can be changed from the app.
- A blue network icon uses an amber dot to show camera activity.

## 1.2.0 — 2026-11-24

- Minimizing to the tray keeps the camera, camera discovery, and approved streams running. The tray icon shows the current camera or session status.
- The user interface, tray menu, messages, launcher scripts, and documentation are in English. Run `Setup.cmd`, `Share.cmd`, `View.cmd`, or `Build.cmd`. Existing launchers remain compatible.

## 1.1.2 — 2026-11-24

- The **Minimize to tray** and Windows minimize buttons leave the icon by the clock. Open the window again from the icon.
- Requests received while the window is hidden remain pending until the host responds or the request expires.

## 1.1.1 — 2026-11-24

- Enabling the connection starts the local preview. The viewer receives video after the host approves sharing.
- The host can pause the camera while waiting for requests. Hiding the window or locking Windows turns off the preview until the host resumes it.

## 1.1.0 — 2026-11-24

- The viewer discovers cameras on the LAN and can connect by IPv4 address and port. The host approves each session and video travels over TLS.
- The viewer remembers the camera's identity after the first approved connection and blocks unexpected changes.
- LAN Camera allows one instance per Windows session, across modes and project folders.
- The host can close the connection from the window or tray icon. Closing releases the configured port and turns off the camera.
- A port conflict shows which port is in use and how to resolve it.

Update both devices to connect without a code.

## 1.0.1 — 2026-11-24

- A random eight-digit private code can include leading zeros.
- The host shows a larger code field and the viewer accepts numeric input.
- SPAKE2 ties pairing to the TLS certificate and keeps the private code off the network.
- The host approves each session and limits connections to five attempts per minute.
- Tests verify encrypted connections, validation, rejection, and rate limiting with simulated video.

Update both devices and run the setup script again.
