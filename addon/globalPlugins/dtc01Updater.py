"""Check GitHub releases for a newer DECtalk DTC-01 add-on.

Uses the public releases API, which needs no authentication or token for a
public repository. On finding a newer release it downloads the attached
.nvda-addon and asks whether to install and restart.

Deliberately conservative about failure: this runs on every NVDA start, and
a synthesizer add-on must never be made less reliable by its update check.
Every network path is wrapped, failures are logged rather than shown, and
the check runs on a daemon thread so it cannot delay or block speech.
"""

import os
import threading
import time
import urllib.request

import addonHandler
import globalPluginHandler
from logHandler import log

ADDON_NAME = "dectalkDtc01"
# Set this to the published repository. Until then the check is inert.
GITHUB_REPO = "borris84/dectalk-dtc01"

RELEASES_URL = "https://api.github.com/repos/%s/releases/latest"
USER_AGENT = "DECtalk DTC-01 NVDA add-on updater"
CHECK_INTERVAL = 24 * 60 * 60      # once a day
STARTUP_DELAY = 30                 # let NVDA settle before any network work
NETWORK_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 180
BLOCK = 8192


def _parseVersion(text):
	"""'v1.2.3' -> (1, 2, 3). Unparseable versions sort lowest so a
	malformed tag can never masquerade as an upgrade."""
	try:
		return tuple(int(p) for p in str(text).strip().lstrip("vV").split("."))
	except Exception:
		return (0,)


def _currentVersion():
	try:
		addon = addonHandler.getCodeAddon()
		return addon.manifest["version"]
	except Exception:
		log.debugWarning("DTC-01 updater: cannot read own version", exc_info=True)
		return None


def _fetchLatest():
	import json
	request = urllib.request.Request(RELEASES_URL % GITHUB_REPO,
									 headers={"User-Agent": USER_AGENT})
	with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
		return json.loads(response.read().decode("utf-8"))


def _addonAsset(release):
	for asset in release.get("assets") or ():
		name = asset.get("name") or ""
		if name.lower().endswith(".nvda-addon"):
			return asset.get("browser_download_url"), name
	return None, None


def _download(url, destination):
	request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
	with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as remote:
		with open(destination, "wb") as local:
			while True:
				block = remote.read(BLOCK)
				if not block:
					break
				local.write(block)


def _offerUpdate(version, url, assetName):
	"""Ask on the main thread; wx and NVDA's dialogs are not thread-safe."""
	import wx
	import gui

	def ask():
		message = _(
			"Version {version} of the DECtalk DTC-01 driver is available. "
			"Download and install it now? NVDA will restart."
		).format(version=version)
		if gui.messageBox(message, _("DECtalk DTC-01 update"),
						  wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
			return
		threading.Thread(target=_installUpdate, args=(url, assetName, version),
						 name="dtc01UpdateDownload", daemon=True).start()

	wx.CallAfter(ask)


def _installUpdate(url, assetName, version):
	import tempfile
	try:
		target = os.path.join(tempfile.gettempdir(), assetName)
		_download(url, target)
	except Exception:
		log.error("DTC-01 updater: download failed", exc_info=True)
		return
	try:
		import wx
		import gui
		# Hand the package to NVDA's own installer rather than unpacking it
		# here, so signature/compatibility handling stays NVDA's job.
		from gui import addonGui
		wx.CallAfter(addonGui.installAddon, gui.mainFrame, target)
		log.info(f"DTC-01 updater: offering {version} from {target}")
	except Exception:
		log.error("DTC-01 updater: could not hand the package to NVDA; "
				  f"it is downloaded at {target}", exc_info=True)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

	def __init__(self):
		super().__init__()
		self._stop = threading.Event()
		if not GITHUB_REPO:
			log.debug("DTC-01 updater: no repository configured, not checking")
			return
		threading.Thread(target=self._loop, name="dtc01Updater",
						 daemon=True).start()

	def terminate(self):
		self._stop.set()
		super().terminate()

	def _loop(self):
		if self._stop.wait(STARTUP_DELAY):
			return
		while not self._stop.is_set():
			try:
				self._checkOnce()
			except Exception:
				log.debugWarning("DTC-01 updater: check failed", exc_info=True)
			if self._stop.wait(CHECK_INTERVAL):
				return

	def _checkOnce(self):
		current = _currentVersion()
		if not current:
			return
		release = _fetchLatest()
		latest = release.get("tag_name") or release.get("name") or ""
		if _parseVersion(latest) <= _parseVersion(current):
			log.debug(f"DTC-01 updater: {current} is current (latest {latest})")
			return
		url, assetName = _addonAsset(release)
		if not url:
			log.debugWarning(f"DTC-01 updater: release {latest} has no "
							 f".nvda-addon attached")
			return
		log.info(f"DTC-01 updater: {latest} available (running {current})")
		_offerUpdate(latest, url, assetName)
