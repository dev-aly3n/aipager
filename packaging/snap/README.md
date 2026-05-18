# Snap — `aipager`

Snap Store manifest. Strict confinement, self-contained (bundles
python + node + `claude` + `dtach` + aipager).

## Install (from Snap Store)

```sh
snap install aipager
aipager config
aipager start
```

The `home`, `network`, and `network-bind` plugs are auto-connected
for strict-confinement snaps — no manual `snap connect` step.

## Known constraints (strict confinement)

1. **Workspace must live under `~/`.** The `home` plug only exposes
   the user's home directory. `/opt/projects/foo` isn't reachable
   from inside the snap; `~/projects/foo` is. Document this in
   user-facing help.
2. **`~/.claude/.credentials.json` is shared with the host.** If
   you have host-installed claude, both the snap and the host
   share the same OAuth credentials.
3. **No host `/tmp` access.** `/tmp/aipager.sock` lives inside the
   snap's namespaced `/tmp`. Internal-only; host processes can't
   read it. Since we bundle claude inside the snap, this is fine.

## Build locally

Requires `snapcraft` (`sudo snap install snapcraft --classic`) and
LXD (`sudo snap install lxd && sudo lxd init --auto`).

```sh
cd packaging/snap
snapcraft pack
# → produces aipager_0.3.12_amd64.snap

# Sideload it for testing:
sudo snap install --dangerous aipager_0.3.12_amd64.snap
```

`--dangerous` skips signature verification, which only the Snap
Store can produce.

## Publish a release

One-time account setup:

```sh
sudo snap install snapcraft --classic
snapcraft login                # links Ubuntu One account in browser
snapcraft register aipager     # claim the name (first release only)
```

Per-release ritual:

```sh
cd packaging/snap
# 1. Bump version: in snapcraft.yaml
sed -i 's/^version: ".*"$/version: "0.3.X"/' snapcraft.yaml

# 2. Build
snapcraft pack

# 3. Upload and release to the stable channel
snapcraft upload --release=stable aipager_0.3.X_amd64.snap
```

Strict-confinement snaps go through Canonical's automated review;
typical turnaround is minutes. The status URL is printed at upload
time — also visible at
[snapcraft.io/aipager/releases](https://snapcraft.io/aipager/releases)
once the name is registered.

## Bump checklist

- [ ] `version:` in `snapcraft.yaml` matches the PyPI release.
- [ ] No new Python deps not listed in `parts.aipager.python-packages`.
- [ ] `snapcraft pack` succeeds locally.
- [ ] `sudo snap install --dangerous` produces a working `aipager`
  binary that prints the right version.
- [ ] `snapcraft upload` accepted by the Store.
