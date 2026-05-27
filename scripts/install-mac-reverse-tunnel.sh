#!/usr/bin/env bash
set -euo pipefail

OPENBROWSER_BROKER_HOST="${OPENBROWSER_BROKER_HOST:-browser-host.example.com}"
OPENBROWSER_BROKER_USER="${OPENBROWSER_BROKER_USER:-root}"
OPENBROWSER_BROKER_REMOTE_SSH_PORT="${OPENBROWSER_BROKER_REMOTE_SSH_PORT:-2222}"
MAC_SSH_PORT="${MAC_SSH_PORT:-22}"
MAC_CHROME_CDP_PORT="${MAC_CHROME_CDP_PORT:-9333}"
LAUNCH_AGENT_DIR="${HOME}/Library/LaunchAgents"
SSH_PLIST="${LAUNCH_AGENT_DIR}/dev.openbrowser-broker.mac-reverse-ssh.plist"
CHROME_PLIST="${LAUNCH_AGENT_DIR}/dev.openbrowser-broker.chrome-cdp.plist"
CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE_ROOT="${HOME}/.hermes/chrome-cdp-clone"
CHROME_PROFILE_DIR="${CHROME_PROFILE_DIR:-Profile 3}"

mkdir -p "${LAUNCH_AGENT_DIR}" "${CHROME_PROFILE_ROOT}"

cat >"${SSH_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>dev.openbrowser-broker.mac-reverse-ssh</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string>
    <string>-N</string>
    <string>-o</string>
    <string>ExitOnForwardFailure=yes</string>
    <string>-o</string>
    <string>ServerAliveInterval=15</string>
    <string>-o</string>
    <string>ServerAliveCountMax=3</string>
    <string>-o</string>
    <string>StrictHostKeyChecking=accept-new</string>
    <string>-R</string>
    <string>127.0.0.1:${OPENBROWSER_BROKER_REMOTE_SSH_PORT}:127.0.0.1:${MAC_SSH_PORT}</string>
    <string>${OPENBROWSER_BROKER_USER}@${OPENBROWSER_BROKER_HOST}</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/dev.openbrowser-broker.mac-reverse-ssh.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/dev.openbrowser-broker.mac-reverse-ssh.err.log</string>
</dict>
</plist>
EOF

cat >"${CHROME_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>dev.openbrowser-broker.chrome-cdp</string>
  <key>ProgramArguments</key>
  <array>
    <string>${CHROME_APP}</string>
    <string>--remote-debugging-port=${MAC_CHROME_CDP_PORT}</string>
    <string>--user-data-dir=${CHROME_PROFILE_ROOT}</string>
    <string>--profile-directory=${CHROME_PROFILE_DIR}</string>
    <string>--no-first-run</string>
    <string>--no-default-browser-check</string>
  </array>
  <key>KeepAlive</key>
  <false/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/dev.openbrowser-broker.chrome-cdp.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/dev.openbrowser-broker.chrome-cdp.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "${SSH_PLIST}" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "${CHROME_PLIST}" >/dev/null 2>&1 || true
for key in GEMINI_API_KEY GOOGLE_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GROQ_API_KEY OPENROUTER_API_KEY NVIDIA_API_KEY; do
  unset "${key}" || true
  launchctl unsetenv "${key}" >/dev/null 2>&1 || true
  launchctl asuser "$(id -u)" launchctl unsetenv "${key}" >/dev/null 2>&1 || true
done
launchctl bootstrap "gui/$(id -u)" "${SSH_PLIST}"
launchctl bootstrap "gui/$(id -u)" "${CHROME_PLIST}"
launchctl enable "gui/$(id -u)/dev.openbrowser-broker.mac-reverse-ssh"
launchctl enable "gui/$(id -u)/dev.openbrowser-broker.chrome-cdp"

echo "Installed ${SSH_PLIST}"
echo "Installed ${CHROME_PLIST}"
launchctl print "gui/$(id -u)/dev.openbrowser-broker.mac-reverse-ssh" | sed -n '1,30p'
launchctl print "gui/$(id -u)/dev.openbrowser-broker.chrome-cdp" | sed -n '1,30p'
