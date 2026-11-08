"""maacli - command-line runner for an installed MaaGakumasu runtime.

Runs the same interface.json tasks the MFAAvalonia GUI runs, without the GUI,
so tasks can be started, stopped and observed from a terminal / scripts.

Run it with the runtime's bundled Python (it has the maafw binding installed):

    Z:\\gakumasu-bot\\MaaGakumasu\\python\\python.exe tools\\maacli.py --runtime Z:\\gakumasu-bot\\MaaGakumasu <command>

Commands
    devices                       list ADB devices MaaFramework can see
    tasks                         list tasks, their options and defaults
    presets                       list presets (tasks/preset.json)
    run [TASK ...] [--preset P]   run tasks; options via --opt 'TASK/OPTION=VALUE'
    stop                          ask a running `run` to stop gracefully
    status                        GUI / CLI run / device status
    logs [--gui|--maafw|--cli] [-n N] [--grep RE]
    screenshot [PATH]             adb screencap of the device

Notes
    * Do not run tasks from the CLI while the GUI is executing tasks on the same
      emulator; keeping the GUI open and idle is fine.
    * Progress lines go to stdout and to <runtime>/logs/cli-<timestamp>.log.
    * MaaFramework's own verbose log is <runtime>/debug/maafw.log (shared with GUI).
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

COLOR_TAG = re.compile(r"\[/?color(?::[^\]]*)?\]")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


# --------------------------------------------------------------------------- helpers
def log_line(fp, text: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    if fp:
        fp.write(line + "\n")
        fp.flush()


def strip_color(s: str) -> str:
    return COLOR_TAG.sub("", s)


def load_interface(runtime: Path) -> dict[str, Any]:
    """interface.json with its `import` files merged (task/option/preset)."""
    with open(runtime / "interface.json", encoding="utf-8") as f:
        base = json.load(f)
    base.setdefault("task", [])
    base.setdefault("option", {})
    base.setdefault("preset", [])
    for rel in base.get("import", []):
        p = runtime / rel
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            sub = json.load(f)
        base["task"].extend(sub.get("task", []))
        base["option"].update(sub.get("option", {}))
        base["preset"].extend(sub.get("preset", []))
    return base


def merge_override(dst: dict, src: dict) -> None:
    """Pipeline-override merge: node-level dict, node keys replaced (PI semantics)."""
    for node, body in (src or {}).items():
        if isinstance(body, dict) and isinstance(dst.get(node), dict):
            dst[node].update(copy.deepcopy(body))
        else:
            dst[node] = copy.deepcopy(body)


def fill_placeholders(obj: Any, values: dict[str, Any]) -> Any:
    if isinstance(obj, dict):
        return {k: fill_placeholders(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_placeholders(v, values) for v in obj]
    if isinstance(obj, str):
        m = re.fullmatch(r"\{([^{}]+)\}", obj)
        if m and m.group(1) in values:
            return values[m.group(1)]
        for k, v in values.items():
            obj = obj.replace("{" + k + "}", str(v))
    return obj


def resolve_option(opt_name: str, options: dict, chosen: dict[str, Any], out: dict, trail: list[str]) -> None:
    """Merge pipeline_override for one option (and nested sub-options) into `out`."""
    opt = options.get(opt_name)
    if opt is None:
        raise SystemExit(f"unknown option: {opt_name}")
    typ = opt.get("type", "select")
    if typ in ("select", "switch", "checkbox"):
        cases = opt.get("cases", [])
        value = chosen.get(opt_name, opt.get("default_case"))
        if typ == "checkbox":
            if value is None:
                selected = []
            elif isinstance(value, str):
                selected = [v.strip() for v in value.split(",") if v.strip()]
            else:
                selected = list(value)
            for c in cases:  # merge in definition order
                if c["name"] in selected:
                    merge_override(out, c.get("pipeline_override", {}))
                    for sub in c.get("option", []):
                        resolve_option(sub, options, chosen, out, trail + [c["name"]])
            trail.append(f"{opt_name}={','.join(selected)}")
            return
        if value is None and cases:
            value = cases[0]["name"]
        if typ == "switch":
            yes = {"yes", "y", "true", "1"}
            v = str(value).strip().lower()
            want = "yes" if v in yes else "no"
            case = next((c for c in cases if c["name"].lower() in (yes if want == "yes" else {"no", "n", "false", "0"})), None)
        else:
            case = next((c for c in cases if c["name"] == value), None)
        if case is None:
            names = [c["name"] for c in cases]
            raise SystemExit(f"option {opt_name!r}: unknown case {value!r}; choices: {names}")
        merge_override(out, case.get("pipeline_override", {}))
        trail.append(f"{opt_name}={case['name']}")
        for sub in case.get("option", []):
            resolve_option(sub, options, chosen, out, trail)
    elif typ == "input":
        values: dict[str, Any] = {}
        inputs = opt.get("inputs", [])
        given = chosen.get(opt_name)
        for inp in inputs:
            name = inp["name"]
            raw = None
            if isinstance(given, dict):
                raw = given.get(name)
            elif given is not None and len(inputs) == 1:
                raw = given
            if raw is None:
                raw = inp.get("default", "")
            ptype = inp.get("pipeline_type", "string")
            if ptype == "int":
                raw = int(raw)
            elif ptype in ("float", "double"):
                raw = float(raw)
            elif ptype == "bool":
                raw = str(raw).lower() in ("1", "true", "yes", "y")
            values[name] = raw
        merge_override(out, fill_placeholders(opt.get("pipeline_override", {}), values))
        trail.append(f"{opt_name}={values}")
    else:
        # hotkey etc. not used by MaaGakumasu; ignore but keep going
        trail.append(f"{opt_name}=(unsupported type {typ}, skipped)")


def build_jobs(iface: dict, task_names: list[str], preset_name: Optional[str], opt_args: list[str], resource_name: str, controller_name: str):
    tasks_by_name = {t["name"]: t for t in iface["task"]}
    chosen_per_task: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    if preset_name:
        preset = next((p for p in iface["preset"] if p["name"] == preset_name), None)
        if preset is None:
            raise SystemExit(f"unknown preset {preset_name!r}; available: {[p['name'] for p in iface['preset']]}")
        for t in preset.get("task", []):
            if not t.get("enabled", True):
                continue
            order.append(t["name"])
            chosen_per_task[t["name"]] = dict(t.get("option", {}))
    for n in task_names:
        if n not in tasks_by_name:
            raise SystemExit(f"unknown task {n!r}; use `tasks` to list")
        if n not in order:
            order.append(n)
        chosen_per_task.setdefault(n, {})
    for spec in opt_args:
        m = re.fullmatch(r"([^/=]+)/([^=]+)=(.*)", spec)
        if not m:
            raise SystemExit(f"--opt must be TASK/OPTION=VALUE (got {spec!r})")
        tname, oname, val = m.group(1), m.group(2), m.group(3)
        if tname not in chosen_per_task:
            raise SystemExit(f"--opt refers to task {tname!r} which is not being run")
        if "." in oname:  # input option field: OPTION.field=value
            oname, field = oname.split(".", 1)
            chosen_per_task[tname].setdefault(oname, {})
            if not isinstance(chosen_per_task[tname][oname], dict):
                chosen_per_task[tname][oname] = {}
            chosen_per_task[tname][oname][field] = val
        else:
            chosen_per_task[tname][oname] = val

    jobs = []
    for n in order:
        t = tasks_by_name[n]
        if t.get("resource") and resource_name not in t["resource"]:
            raise SystemExit(f"task {n!r} is not available for resource {resource_name!r} (needs {t['resource']})")
        if t.get("controller") and controller_name not in t["controller"]:
            raise SystemExit(f"task {n!r} is not available for controller {controller_name!r}")
        override: dict = {}
        merge_override(override, t.get("pipeline_override", {}))
        trail: list[str] = []
        for oname in t.get("option", []) or []:
            resolve_option(oname, iface["option"], chosen_per_task[n], override, trail)
        jobs.append({"name": n, "entry": t["entry"], "override": override, "options": trail})
    return jobs


# --------------------------------------------------------------------------- commands
def cmd_devices(args, runtime: Path) -> int:
    from maa.toolkit import Toolkit

    devs = Toolkit.find_adb_devices()
    if not devs:
        print("no ADB devices found (is the emulator running?)")
        return 1
    for d in devs:
        print(f"{d.address}\t{d.name}\tadb={d.adb_path}\tscreencap={d.screencap_methods}\tconfig={json.dumps(d.config, ensure_ascii=False)}")
    return 0


def cmd_tasks(args, runtime: Path) -> int:
    iface = load_interface(runtime)
    for t in iface["task"]:
        scope = []
        if t.get("resource"):
            scope.append("resource=" + "/".join(t["resource"]))
        if t.get("controller"):
            scope.append("controller=" + "/".join(t["controller"]))
        print(f"{t['name']}\tentry={t['entry']}\t{' '.join(scope)}")
        for oname in t.get("option", []) or []:
            o = iface["option"].get(oname, {})
            typ = o.get("type", "select")
            if typ in ("select", "switch", "checkbox"):
                cases = [c["name"] for c in o.get("cases", [])]
                print(f"    - {oname} [{typ}] default={o.get('default_case')!r} cases={cases}")
                for c in o.get("cases", []):
                    for sub in c.get("option", []):
                        so = iface["option"].get(sub, {})
                        print(f"        ({c['name']}) -> {sub} [{so.get('type','select')}] default={so.get('default_case', [i.get('default') for i in so.get('inputs', [])])!r}")
            elif typ == "input":
                print(f"    - {oname} [input] fields={[(i['name'], i.get('default')) for i in o.get('inputs', [])]}")
            else:
                print(f"    - {oname} [{typ}]")
    return 0


def cmd_presets(args, runtime: Path) -> int:
    iface = load_interface(runtime)
    for p in iface["preset"]:
        print(p["name"])
        for t in p.get("task", []):
            flag = "x" if t.get("enabled", True) else " "
            print(f"  [{flag}] {t['name']}  {json.dumps(t.get('option', {}), ensure_ascii=False)}")
    return 0


def pid_file(runtime: Path) -> Path:
    return runtime / "logs" / "cli.pid"


def stop_file(runtime: Path) -> Path:
    return runtime / "logs" / "cli.stop"


def cli_running(runtime: Path) -> Optional[int]:
    p = pid_file(runtime)
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip())
    except ValueError:
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        if str(pid) in out:
            return pid
    except Exception:
        pass
    return None


def cmd_stop(args, runtime: Path) -> int:
    pid = cli_running(runtime)
    if pid is None:
        print("no CLI run in progress")
        return 1
    stop_file(runtime).write_text("stop")
    print(f"stop requested for pid {pid}")
    return 0


def cmd_status(args, runtime: Path) -> int:
    gui = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MaaGakumasu.exe", "/NH"], capture_output=True, text=True).stdout
    print("GUI:", "running" if "MaaGakumasu.exe" in gui else "not running")
    pid = cli_running(runtime)
    print("CLI run:", f"running (pid {pid})" if pid else "idle")
    logs = sorted(glob.glob(str(runtime / "logs" / "cli-*.log")))
    if logs:
        last = Path(logs[-1])
        tail = last.read_text(encoding="utf-8", errors="replace").splitlines()[-1:] or [""]
        print(f"last CLI log: {last.name} | {tail[0]}")
    return cmd_devices(args, runtime)


def cmd_logs(args, runtime: Path) -> int:
    if args.maafw:
        files = [runtime / "debug" / "maafw.log"]
    elif args.gui:
        files = sorted(Path(p) for p in glob.glob(str(runtime / "logs" / "log-*.log")))[-1:]
    else:
        files = sorted(Path(p) for p in glob.glob(str(runtime / "logs" / "cli-*.log")))[-1:]
        if not files:
            files = sorted(Path(p) for p in glob.glob(str(runtime / "logs" / "log-*.log")))[-1:]
    if not files or not files[0].exists():
        print("no log file found")
        return 1
    lines = files[0].read_text(encoding="utf-8", errors="replace").splitlines()
    if args.grep:
        rx = re.compile(args.grep)
        lines = [l for l in lines if rx.search(l)]
    elif args.gui and not args.raw:
        lines = [l for l in lines if "MonitorLog" in l or "[ERR]" in l]
        lines = [re.sub(r"\[cfg=[^\]]*\]\[inst=[^\]]*\]\[src=[^\]]*\]\[op=[^\]]*\] ", "", l) for l in lines]
    print(f"== {files[0]}")
    for l in lines[-args.n:]:
        print(l[: args.width] if args.width else l)
    return 0


def cmd_screenshot(args, runtime: Path) -> int:
    from maa.toolkit import Toolkit

    devs = Toolkit.find_adb_devices()
    if not devs:
        print("no device")
        return 1
    d = devs[0]
    out = Path(args.path) if args.path else runtime / "logs" / f"screen-{datetime.now():%Y%m%d-%H%M%S}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        r = subprocess.run([str(d.adb_path), "-s", d.address, "exec-out", "screencap", "-p"], stdout=f)
    if r.returncode != 0 or out.stat().st_size == 0:
        print("screencap failed")
        return 1
    print(out)
    return 0


def cmd_run(args, runtime: Path) -> int:
    iface = load_interface(runtime)
    controller_def = next((c for c in iface["controller"] if c.get("type") == "Adb"), None)
    if controller_def is None:
        raise SystemExit("interface.json has no Adb controller")
    resource_def = next((r for r in iface["resource"] if r["name"] == args.resource), None)
    if resource_def is None:
        raise SystemExit(f"unknown resource {args.resource!r}; available: {[r['name'] for r in iface['resource']]}")

    jobs = build_jobs(iface, args.task, args.preset, args.opt or [], args.resource, controller_def["name"])
    if not jobs:
        raise SystemExit("nothing to run: give task names or --preset")

    if args.dry_run:
        for j in jobs:
            print(f"{j['name']} (entry {j['entry']})")
            for t in j["options"]:
                print(f"    {t}")
            if args.verbose:
                print("    override:", json.dumps(j["override"], ensure_ascii=False))
        return 0

    if cli_running(runtime):
        raise SystemExit("another CLI run is in progress (logs/cli.pid); use `stop` first")

    from maa.agent_client import AgentClient
    from maa.context import ContextEventSink
    from maa.controller import AdbController
    from maa.resource import Resource
    from maa.tasker import Tasker, TaskerEventSink
    from maa.toolkit import Toolkit

    os.chdir(runtime)
    Toolkit.init_option(str(runtime))
    (runtime / "logs").mkdir(exist_ok=True)
    log_path = runtime / "logs" / f"cli-{datetime.now():%Y%m%d-%H%M%S}.log"
    fp = open(log_path, "a", encoding="utf-8")
    pid_file(runtime).write_text(str(os.getpid()))
    if stop_file(runtime).exists():
        stop_file(runtime).unlink()
    log = lambda s: log_line(fp, s)  # noqa: E731
    log(f"maacli run | runtime={runtime} | resource={args.resource} | tasks={[j['name'] for j in jobs]}")
    log(f"log file: {log_path}")

    agent_proc = None
    agent = None
    failed: list[str] = []
    try:
        # ---- controller
        devs = Toolkit.find_adb_devices()
        if args.device:
            devs = [d for d in devs if d.address == args.device]
        if not devs:
            log("ERROR: no ADB device found" + (f" matching {args.device}" if args.device else ""))
            return 2
        dev = devs[0]
        log(f"device: {dev.name} {dev.address} (adb {dev.adb_path})")
        controller = AdbController(dev.adb_path, dev.address, dev.screencap_methods, dev.input_methods, dev.config)
        if not controller.post_connection().wait().succeeded:
            log("ERROR: controller connection failed")
            return 2
        log("controller connected")

        # ---- resource
        resource = Resource()
        for rel in resource_def["path"]:
            p = (runtime / rel).resolve()
            if not resource.post_bundle(p).wait().succeeded:
                log(f"ERROR: failed to load resource bundle {p}")
                return 2
            log(f"resource loaded: {p}")
        if not args.cpu:
            resource.use_auto_ep()

        # ---- agent (custom recognition/action in python)
        agent_cfgs = iface.get("agent")
        if agent_cfgs and not args.no_agent:
            if isinstance(agent_cfgs, dict):
                agent_cfgs = [agent_cfgs]
            if len(agent_cfgs) > 1:
                log("WARNING: multiple agents defined; only the first is started")
            acfg = agent_cfgs[0]
            agent = AgentClient()
            agent.bind(resource)
            ident = agent.identifier
            exe = acfg["child_exec"]
            exe_path = (runtime / exe).resolve() if exe.startswith(".") else exe
            cmd = [str(exe_path)] + list(acfg.get("child_args", [])) + [ident]
            env = dict(os.environ)
            env.update({
                "PI_CURRENT_CONTROLLER": controller_def["name"],
                "PI_CURRENT_RESOURCE": args.resource,
                "PI_CLIENT_NAME": "maacli",
                # keep the agent's stdout readable when piped (UTF-8, no ANSI colors)
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "LOGURU_COLORIZE": "NO",
                "NO_COLOR": "1",
            })
            log(f"starting agent: {' '.join(cmd)}")
            agent_proc = subprocess.Popen(cmd, cwd=runtime, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)

            def pump():
                for line in agent_proc.stdout:  # type: ignore[union-attr]
                    line = ANSI.sub("", line).rstrip()
                    if line:
                        log_line(fp, f"[agent] {line}")

            threading.Thread(target=pump, daemon=True).start()
            agent.set_timeout(120_000)
            if not agent.connect():
                log("ERROR: agent did not connect (see [agent] lines above)")
                return 2
            log(f"agent connected: actions={agent.custom_action_list} recognitions={agent.custom_recognition_list}")

        # ---- tasker + progress sink
        tasker = Tasker()
        if not tasker.bind(resource, controller) or not tasker.inited:
            log("ERROR: tasker init failed")
            return 2
        tasker.set_save_on_error(True)

        class Progress(ContextEventSink):
            def on_raw_notification(self, context, msg, details):
                try:
                    name = details.get("name", "")
                    focus = details.get("focus")
                    if isinstance(focus, dict):
                        text = focus.get(msg) or focus.get(msg.split(".", 1)[-1])
                        if isinstance(text, str):
                            log(f"  {strip_color(text)}")
                        elif isinstance(text, list):
                            for t in text:
                                log(f"  {strip_color(str(t))}")
                    elif isinstance(focus, str) and msg.endswith("Succeeded"):
                        log(f"  {strip_color(focus)}")
                    if args.verbose and msg in ("Node.PipelineNode.Starting", "Node.PipelineNode.Failed"):
                        log(f"  [{msg.split('.')[-1]}] {name}")
                    elif msg == "Node.PipelineNode.Failed":
                        log(f"  node failed: {name}")
                except Exception as e:  # never let a sink exception kill the run
                    log(f"  (sink error: {e})")

        class TaskWatch(TaskerEventSink):
            def on_tasker_task(self, tasker, noti_type, detail):
                log(f"task {detail.entry}: {noti_type.name}")

        tasker.add_context_sink(Progress())
        tasker.add_sink(TaskWatch())

        # ---- run
        stopped = False
        for j in jobs:
            if stopped:
                break
            log(f"=== start {j['name']} (entry {j['entry']}) options={j['options']}")
            job = tasker.post_task(j["entry"], j["override"])
            while not job.done:
                if stop_file(runtime).exists():
                    log("stop requested; stopping tasker ...")
                    tasker.post_stop().wait()
                    stopped = True
                    break
                time.sleep(0.5)
            if stopped:
                log(f"=== {j['name']} stopped")
                failed.append(j["name"])
                break
            if job.succeeded:
                log(f"=== {j['name']} OK")
            else:
                log(f"=== {j['name']} FAILED")
                failed.append(j["name"])
                if args.fail_fast:
                    break
        if stopped:
            log("run stopped by request")
        elif failed:
            log(f"run finished with failures: {failed}")
        else:
            log("run finished: all tasks OK")
        return 1 if failed else 0
    except KeyboardInterrupt:
        log("interrupted; stopping ...")
        try:
            tasker.post_stop().wait()  # type: ignore[name-defined]
        except Exception:
            pass
        return 130
    finally:
        try:
            if agent is not None:
                agent.disconnect()
        except Exception:
            pass
        if agent_proc is not None:
            try:
                agent_proc.terminate()
                agent_proc.wait(timeout=5)
            except Exception:
                agent_proc.kill()
        for p in (pid_file(runtime), stop_file(runtime)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        fp.close()


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(prog="maacli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime", default=os.environ.get("MAA_RUNTIME", r"Z:\gakumasu-bot\MaaGakumasu"), help="installed MaaGakumasu directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices")
    sub.add_parser("tasks")
    sub.add_parser("presets")
    sub.add_parser("stop")
    sub.add_parser("status")

    pr = sub.add_parser("run")
    pr.add_argument("task", nargs="*", help="task names as shown by `tasks`")
    pr.add_argument("--preset", help="preset name as shown by `presets`")
    pr.add_argument("--opt", action="append", help="TASK/OPTION=VALUE (checkbox: comma-separated; input field: OPTION.field=VALUE)")
    pr.add_argument("--resource", default="官服")
    pr.add_argument("--device", help="ADB address, e.g. 127.0.0.1:16384 (default: first device)")
    pr.add_argument("--no-agent", action="store_true", help="do not start the python agent (tasks using custom nodes will fail)")
    pr.add_argument("--cpu", action="store_true", help="disable GPU inference")
    pr.add_argument("--fail-fast", action="store_true", help="stop at the first failed task (default: continue like the GUI)")
    pr.add_argument("--dry-run", action="store_true", help="only print the resolved task list and options")
    pr.add_argument("-v", "--verbose", action="store_true")

    pl = sub.add_parser("logs")
    pl.add_argument("-n", type=int, default=40)
    pl.add_argument("--gui", action="store_true", help="GUI log (logs/log-*.log), MonitorLog/ERR lines")
    pl.add_argument("--maafw", action="store_true", help="MaaFramework log (debug/maafw.log)")
    pl.add_argument("--raw", action="store_true", help="no filtering for --gui")
    pl.add_argument("--grep", help="regex filter")
    pl.add_argument("--width", type=int, default=240)

    ps = sub.add_parser("screenshot")
    ps.add_argument("path", nargs="?")

    args = ap.parse_args()
    runtime = Path(args.runtime).resolve()
    if not (runtime / "interface.json").exists():
        raise SystemExit(f"{runtime} is not a MaaGakumasu runtime (interface.json missing)")
    return {
        "devices": cmd_devices,
        "tasks": cmd_tasks,
        "presets": cmd_presets,
        "run": cmd_run,
        "stop": cmd_stop,
        "status": cmd_status,
        "logs": cmd_logs,
        "screenshot": cmd_screenshot,
    }[args.cmd](args, runtime)


if __name__ == "__main__":
    raise SystemExit(main())
