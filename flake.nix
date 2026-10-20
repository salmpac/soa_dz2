{
  description = "Marketplace API (FastAPI + PostgreSQL) dev environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            python312
            uv
            postgresql   # psql client for demos
            docker-compose
            jq
            curl
          ];
          shellHook = ''
            export UV_NO_MANAGED_PYTHON=1
            if [ ! -d .venv ]; then
              uv venv -q --python "${pkgs.python312}/bin/python3" .venv
              uv pip install -q --python .venv/bin/python -r requirements.txt
            fi
            source .venv/bin/activate
            echo "venv ready. make generate -> make run"
          '';
        };
      });
    };
}
