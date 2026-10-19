# Step-by-step install guide (no experience needed)

This guide takes you from nothing to a working NAS Control Panel in about **10 minutes**. You don't need to know anything about Docker or GitHub. You'll copy one settings file, paste it into your NAS, change four lines, and press **Deploy**.

The screenshots are from a **UGREEN NAS (UGOS Pro)**. Other brands work the same way; see [Other NAS brands](#other-nas-brands) for where their buttons are.

**You need:**
- A NAS with the **Docker** app installed (UGREEN: App Center → Docker)
- A computer on the same home network, with a web browser
- About 10 minutes

---

## Step 1 — Copy the settings file from GitHub

1. Go to **https://github.com/ibiddle/nas-panel**
2. In the list of files, click **compose.yaml**.

   ![Click compose.yaml](img/gh-1-repo.png)

3. Click the **copy** button (two overlapping squares) at the top right of the file. The whole file is now copied, ready to paste.

   ![Click the copy button](img/gh-2-copy.png)

> You don't need a GitHub account, and you don't download anything else. Your NAS fetches the app itself in Step 4.

---

## Step 2 — Create a "Project" in your NAS's Docker app

1. Log in to your NAS in a web browser and open the **Docker** app.
2. Click **Project** on the left, then click **Create** ①.

   ![Docker → Project → Create](img/ugos-1-project.png)

3. Fill in the form:
   - ① **Name:** `nas-panel`
   - ② **Storage path:** click the folder icon and choose your **docker** shared folder. Create a new folder inside it called **nas-panel** and select it.
   - ③ **Compose configuration:** click inside the big box and paste with **Ctrl + V** (on a Mac, **Cmd + V**).

   ![Fill in name, folder, and paste](img/ugos-2-create.png)

---

## Step 3 — Change the four settings lines

Scroll down a little in the box you just pasted into. The lines you need to look at end in **`<-- CHANGE`**.

![The lines to change](img/ugos-3-settings.png)

| Line | What to put | How to find it |
|---|---|---|
| `TZ` | Your time zone, e.g. `Europe/London`, `America/New_York`, `Australia/Sydney` | [List of time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (use the "TZ identifier" column) |
| `PUID` | Your NAS user's ID number | **UGREEN shows it for you** just above the box: *PUID=1000 PGID=10* (top-left box in the picture). Copy the first number |
| `PGID` | Your NAS user's group number | The second number from the same place, e.g. `10` |
| `APPS_DIRS` | The folder your Docker apps are in | UGREEN: leave as `/volume1/docker`, which is the shared folder called **docker** |

**Two more rules:**
- If you change `APPS_DIRS`, change the matching `volumes` line (bottom box in the picture) to the **same path on both sides of the colon**, e.g. `/volume1/docker:/volume1/docker`.
- Further down, in the `updater` section, set `TZ` to the same time zone again.

Leave everything else exactly as it is. Spaces at the start of lines matter, so don't add or remove any.

---

## Step 4 — Deploy

Make sure **Run immediately after creation** is ticked, then click **Deploy**.

![Deploy](img/ugos-4-deploy.png)

The NAS now downloads the app from GitHub, which takes a minute or two. When it's done, go to **Container** on the left. You'll see two new containers, both **Running**:

![Both containers running](img/ugos-5-running.png)

- **nas-panel** is the panel itself.
- **nas-panel-updater** checks for a new version every night and updates the panel for you. You don't need to do anything with it.

---

## Step 5 — Get your one-time setup code

1. In **Container**, click **nas-panel**, then open the **Log** tab ①.
2. Look for a box like this near the top (or type `SETUP` in the **Search** box ②):

   ```
   ==================================================
     NAS PANEL SETUP CODE:  4F7A19C2
     Open http://<your-NAS-IP>:8090 and enter this code
   ==================================================
   ```

   ![Container → nas-panel → Log](img/ugos-6-log.png)

The code only works once, and only until you've set a password. Without it, nobody else on your network can claim your panel first.

---

## Step 6 — Open the panel and choose a password

1. In your browser, go to **`http://YOUR-NAS-IP:8090`**. Use the same address you use for your NAS, with `:8090` on the end. For example, if your NAS is `http://192.168.1.50:9999`, the panel is at `http://192.168.1.50:8090`.
2. Enter the **setup code** and choose a **password** (at least 8 characters, ideally longer).

   ![First-time setup](img/panel-setup.png)

3. You're in:

   ![The home page](img/panel-home.png)

**About the password:** 5 wrong passwords within 10 minutes locks the panel for 10 minutes. If you forget your password, see [Forgotten password](../README.md#forgotten-password).

---

## Step 7 (optional) — Put an icon on your NAS desktop

1. In **Docker → Container**, click the **···** button next to **nas-panel** ①, then **Desktop shortcut** ②.

   ![More → Desktop shortcut](img/ugos-7-menu.png)

2. The port is already filled in (**8090**). Click **Confirm**. You can choose **Custom** to pick a nicer icon.

   ![Desktop shortcut](img/ugos-8-shortcut.png)

From now on, double-click the icon on your NAS desktop to open the panel.

---

## Using it: connect a folder from another NAS or PC to Jellyfin / Plex

**Before you start, on the other NAS or PC** (the one that has the films):
- Turn on file sharing (SMB / "Windows file sharing").
- Create a user that can **read** the shared folder, for example `jellyfin-read`. Don't use your admin account.
- Write down its IP address and the shared folder's exact name.
- Give it a fixed IP: in your router, look for "DHCP reservation" or "address reservation".

**1. Save the share.** Open **Network Shares**, click **➕ Add a share** and fill it in. Click **Test & save**: the panel checks it can connect before saving anything.

![Add a share](img/panel-add-share.png)

**2. Connect it to an app.** Choose the share and the app, then click **Connect**. The app restarts for a few seconds. If anything goes wrong, it's put back exactly as it was.

![Connect a share to an app](img/panel-connect.png)

**3. Check it.** The share appears under **What's connected**:

![What's connected](img/panel-shares.png)

**4. Tell the app about the folder.** For example, in **Jellyfin** go to **Dashboard → Libraries → (your library) → Manage folders → +** and choose `/media/network/films` (whatever short name you gave the share). In **Plex** go to **Settings → Libraries → Edit → Add folders**.

The share is **read-only**: Jellyfin, Plex and other apps can play the files but can never change or delete them.

---

## Other NAS brands

The steps are the same. Only the button names differ:

| NAS | Where to create the project | Where to find PUID / PGID |
|---|---|---|
| **UGREEN** (UGOS Pro) | Docker → Project → Create | Shown above the compose box |
| **Synology** (DSM 7.2+) | Container Manager → Project → Create → "Create docker-compose.yml" | Usually `1026` / `100` for the first user. Exact numbers: Control Panel → Terminal & SNMP → enable SSH, then run `id yourname` |
| **QNAP** (QTS) | Container Station → Applications → Create | `id yourname` over SSH. Change `/volume1/docker` to your QNAP path, e.g. `/share/Container` |
| **Unraid** | Install the **Docker Compose Manager** plugin → Add new stack | Usually `99` / `100`. Change paths to `/mnt/user/appdata` |
| **Anything else with Docker** | Save the file as `compose.yaml` in a folder, then run `docker compose up -d` in that folder | `id yourname` |

---

## Something not working?

| Problem | Fix |
|---|---|
| Deploy fails with a "yaml" or "indentation" error | The paste went wrong. Delete everything in the box and paste again. Don't add or remove spaces at the start of lines |
| Container won't start, log says *Docker socket not found* | The `/var/run/docker.sock` line was changed or deleted. Paste the file again |
| Browser says *can't reach this page* | Check the address ends in `:8090` and that **nas-panel** shows **Running** |
| The panel shows *No Docker Compose apps found* | `APPS_DIRS` and its `volumes` line must point at the folder your apps are in, with the same path on both sides. Apps must have been created as **Projects**, not single containers |
| A share won't connect | The message tells you why (wrong password, wrong share name, can't reach it…). The **NAS Shares → Test a share first** form lets you try settings without saving them |
| Locked out after wrong passwords | Wait 10 minutes |

Still stuck? Open an issue at **https://github.com/ibiddle/nas-panel/issues** and include what you see in the **Log** tab (hide any passwords).
