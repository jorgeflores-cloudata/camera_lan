# LAN Camera

Current version **1.2.0**. See the [changelog](CHANGELOG.md).

Connect two devices to the same local network. Run **Share** on the Windows 11 device with the camera and **View** on the other device.

## Set up both devices

1. Extract the project folder on both devices.
2. Use standard 64-bit Python 3.12 to 3.15 for Windows with Tcl/Tk.
3. Run **Setup.cmd** on each device to download dependencies. Run it again when updating a previously prepared copy. Video travels over the LAN.

If **Setup.cmd** reports a Tcl/Tk problem, run `py -3 -m tkinter` to check for the demo window. If your installation has only the `python` command, run `python -m tkinter`.

## Share and view

1. On the device with the camera, run **Share.cmd**. Choose its local IPv4 address and port. `ipconfig` shows the address. The default port is `8443`.
2. Click **Enable connection** to start the local preview and wait for requests. If Windows shows a firewall prompt, allow access on the private network you are using.
3. On the other device, run **View.cmd**. Click **Find cameras** and pick the camera. If it is missing from the list, enter the host's IPv4 address and port. Click **Request access**. Use version **1.1.0 or later** on both devices.
4. At the host, check the request and click **Accept and share** to send video to the viewer.
5. If the host enabled viewer controls, the viewer can pause, resume, and adjust quality and frame rate within local limits. Either device can end the session.

**Minimize to tray** and the Windows minimize button hide the window and leave the icon by the clock. The camera, an approved stream, discovery, and viewer controls continue in the background. New requests wait for you to choose **Open** from the icon. A red icon indicates that the camera is on or starting.

Choose **Close connection** in the host window or on the tray icon to stop the camera, disconnect the viewer, and release the port. The title-bar **X** or locking Windows turns off the camera and ends an active session. If you were waiting for requests, click **Resume camera** after returning.

LAN Camera runs once per Windows session. To switch modes or load an update, choose **Exit** on the tray icon before starting **Share.cmd** again.

Video travels over encrypted TLS to one viewer at a time. A session can last up to 30 minutes and the viewer receives live video only.

Accept requests from the device you expect. After the first approved connection, the viewer remembers the camera's identity for that IP address and port. If it changes, the viewer blocks the connection and explains the change. After verifying an expected change, click **Forget identity** and request a new connection.

The host allows up to five connection attempts per minute. If you reach the limit, wait a minute before trying again.

If the host cannot open the camera, go to **Settings → Privacy & security → Camera** and allow desktop apps to access it. Discovery uses IPv4 multicast over UDP port `55443`. If your network blocks multicast, connect using the host's IPv4 address and port. Guest networks may prevent devices from reaching each other.

## Build a Windows executable

After setup, run **Build.cmd** on 64-bit Windows 11. The output is `dist\CamaraLAN.exe`. Open it to choose **Share camera** or **View and control**.
