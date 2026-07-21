{
  description = "Telegram remote-control daemon for Claude Code CLI sessions";

  inputs = {
    nixpkgs.url     = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs   = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;

        aipager = python.pkgs.buildPythonApplication {
          pname  = "aipager";
          # Keep in sync with pyproject.toml [project].version.
          version = "0.4.22";
          format = "pyproject";

          src = ./.;

          # Drop the dtach-bin PyPI dep — we use Nix's own dtach from
          # nixpkgs instead of the bundled-binary wheel. Mirrors the
          # inreplace in the Homebrew formula.
          postPatch = ''
            substituteInPlace pyproject.toml \
              --replace-fail '"dtach-bin>=0.9.1",' ""
          '';

          nativeBuildInputs = [ pkgs.makeWrapper ];

          build-system = with python.pkgs; [ hatchling ];

          dependencies = with python.pkgs; [
            python-telegram-bot
            rich
            questionary
            pyyaml
          ];

          # Put dtach on PATH at runtime so dtach_inject's
          # shutil.which("dtach") finds it. `claude` stays out-of-tree
          # — users install it via npm / their preferred path.
          makeWrapperArgs = [
            "--prefix" "PATH" ":"
            "${pkgs.lib.makeBinPath [ pkgs.dtach ]}"
          ];

          # Test suite touches /tmp/aipager.sock and spawns
          # subprocesses, which the Nix build sandbox blocks. The
          # GitHub Actions test workflow covers full pytest on every
          # push.
          doCheck = false;

          meta = with pkgs.lib; {
            description = "Telegram remote-control daemon for Claude Code CLI sessions";
            homepage    = "https://aipager.run";
            license     = licenses.mit;
            mainProgram = "aipager";
            platforms   = platforms.unix;
          };
        };
      in {
        packages.default = aipager;
        packages.aipager = aipager;

        apps.default = {
          type    = "app";
          program = "${aipager}/bin/aipager";
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
            pkgs.dtach
            pkgs.ruff
            pkgs.uv
          ] ++ (with python.pkgs; [
            pytest
            python-telegram-bot
            rich
            questionary
            pyyaml
            hatchling
            build
          ]);
        };

        formatter = pkgs.nixpkgs-fmt;
      });
}
