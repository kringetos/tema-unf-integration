"""Explicit 1C Designer operations for the isolated TEMA_API extension."""
import argparse
from pathlib import Path
import subprocess

from .server import load_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["load", "check", "export", "activate"])
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    settings = load_settings(args.config)
    directory = Path(args.config).resolve().parent
    source = Path(__file__).resolve().parent / "bridge"
    commands = {
        "load": ["/LoadConfigFromFiles", str(source), "-Extension", "TEMA_API"],
        "check": ["/CheckConfig", "-Extension", "TEMA_API", "-Server", "-ThinClient"],
        "export": ["/DumpCfg", str(directory / "TEMA_API.cfe"), "-Extension", "TEMA_API"],
        "activate": ["/UpdateDBCfg", "-Extension", "TEMA_API"],
    }
    log = directory / (args.operation + "-extension.log")
    command = [settings["platform"], "DESIGNER", "/F", settings["infobase"], "/N", settings["odata_user"],
               "/P", settings.get("odata_password", ""), "/DisableStartupMessages", "/DisableStartupDialogs",
               *commands[args.operation], "/Out", str(log)]
    result = subprocess.run(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print(args.operation, "exit", result.returncode, "log:", log)
    if log.exists():
        print(log.read_text(encoding="utf-8-sig", errors="replace")[-12000:])
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
