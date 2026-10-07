{
  description = "keyed james bible — key a question into a span of KJV verses via Jev";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux" "aarch64-linux"];
    forAllSystems = nixpkgs.lib.genAttrs systems;

    # A question is keyed into the Bible by chaining Jev Choice questions down
    # book -> chapter -> verse. Jev returns typed answers with probabilities
    # instead of text, so nothing here generates scripture: the verse text is
    # clipped from the KJV corpus, which is built from the public-domain
    # per-book JSON files in data/kjv/.
    #
    # Pure Python standard library, so no dependencies to declare.
    packageFor = pkgs:
      pkgs.stdenv.mkDerivation {
        pname = "keyed-james-bible";
        version = "0.1.0";

        src = pkgs.lib.cleanSourceWith {
          src = ./.;
          filter = path: type: let
            base = baseNameOf (toString path);
          in
            !(
              # Never ship a secret into the store.
              base == ".env"
              || base == "result"
              || base == ".direnv"
              || base == "experiments"
              || base == "__pycache__"
              || base == "cache.json"
            );
        };

        nativeBuildInputs = [pkgs.makeWrapper pkgs.python3];

        # Build the corpus during the build. scripts/build_corpus.py asserts the
        # canonical KJV structure (66 books, 1189 chapters, 31102 verses), so a
        # truncated or corrupted source tree fails the build here rather than
        # shipping a corpus that silently resolves verses wrongly.
        buildPhase = ''
          runHook preBuild
          python3 scripts/build_corpus.py
          runHook postBuild
        '';

        installPhase = ''
          runHook preInstall

          # Everything is installed at one level with the same relative layout it
          # has in the source tree. That is deliberate: corpus.py and resolver.py
          # locate the corpus as <parent of jev_kjv>/data/kjv.json, and server.py
          # locates the web assets as <dir of server.py>/web. Preserving the
          # layout keeps all three assumptions true with no path rewiring.
          appdir=$out/lib/keyed-james-bible
          mkdir -p $appdir/data
          cp -r jev_kjv ask.py server.py web $appdir/
          rm -rf $appdir/jev_kjv/__pycache__
          install -Dm444 data/kjv.json $appdir/data/kjv.json

          mkdir -p $out/bin
          makeWrapper ${pkgs.python3}/bin/python3 $out/bin/keyed-james-bible \
            --add-flags "$appdir/server.py" \
            --set-default PYTHONPATH "$appdir"
          makeWrapper ${pkgs.python3}/bin/python3 $out/bin/ask \
            --add-flags "$appdir/ask.py" \
            --set-default PYTHONPATH "$appdir"

          runHook postInstall
        '';

        meta = with pkgs.lib; {
          description = "Key a question into a span of King James Version verses using Jev";
          homepage = "https://github.com/mccartykim/keyed_james_bible";
          license = licenses.mit;
          mainProgram = "keyed-james-bible";
        };
      };
  in {
    packages = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      default = packageFor pkgs;
      keyed-james-bible = packageFor pkgs;
    });

    apps = forAllSystems (system: {
      default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/keyed-james-bible";
      };
    });

    # Consumed by systems-flake on historian as a host service, the way borges
    # and media-classifier are. Deliberately NOT a NixOS module: the systemd unit
    # lives in systems-flake's hosts/historian/kjb.nix, so the agenix secret path
    # and the state directory stay in the fleet config beside the service
    # registry entry that gates them.
    devShells = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      default = pkgs.mkShell {
        packages = [pkgs.python3];
      };
    });
  };
}
