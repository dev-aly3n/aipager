# AUR — `aipager`

Arch Linux build recipe. Mirrored to
[`aur.archlinux.org/aipager.git`](https://aur.archlinux.org/packages/aipager)
on each release.

## Install (from AUR)

```sh
yay -S aipager       # or paru, pikaur, etc.
```

Or build by hand from this directory after a `git clone` of the
aipager repo:

```sh
cd packaging/aur
makepkg -si
```

System `dtach` and `python-telegram-bot` come from pacman; the
Anthropic `claude` CLI is **not** in pacman, so install it
separately:

```sh
sudo pacman -S npm
sudo npm install -g @anthropic-ai/claude-code
```

After install:

```sh
aipager config       # interactive setup
aipager start        # foreground daemon, or `aipager service install`
```

## Bump a release

Update both `PKGBUILD` and `.SRCINFO` then push to AUR.

1. **Bump `PKGBUILD`**:
   - `pkgver=` to the new release.
   - `sha256sums=()` to the new sdist's sha256, from
     `https://pypi.org/pypi/aipager/<version>/json` (read
     `urls[].digests.sha256` for the `sdist` entry).
   - Reset `pkgrel=1` (it stays 1 unless we change the recipe
     without bumping the upstream version).

2. **Regenerate `.SRCINFO`** on any Arch box (or in a container):
   ```sh
   cd packaging/aur
   makepkg --printsrcinfo > .SRCINFO
   ```

3. **Commit to the aipager repo** (canonical source):
   ```sh
   git add packaging/aur/PKGBUILD packaging/aur/.SRCINFO
   git commit -m "aur: bump to 0.3.X"
   git push
   ```

4. **Push to AUR** (separate repo, SSH key auth):
   ```sh
   TMP=/tmp/aur-aipager
   rm -rf "$TMP"
   git clone ssh://aur@aur.archlinux.org/aipager.git "$TMP"
   cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO "$TMP/"
   cd "$TMP"
   git add PKGBUILD .SRCINFO
   git commit -m "aipager 0.3.X"
   git push
   ```

## One-time AUR account setup

If your account / SSH key isn't set up yet:

1. Register at https://aur.archlinux.org/register.
2. Generate an SSH key dedicated to AUR:
   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/aur_ed25519 -C "aur@aipager"
   ```
3. Paste the contents of `~/.ssh/aur_ed25519.pub` into your AUR
   account profile (My Account → SSH Public Key).
4. Add an `~/.ssh/config` block:
   ```
   Host aur.archlinux.org
     IdentityFile ~/.ssh/aur_ed25519
     User aur
   ```
5. Test:
   ```sh
   ssh aur@aur.archlinux.org
   # → "Hi <username>! ..." means it's wired up.
   ```
