import logging
import os
import subprocess
import sys
import threading
import time

from django.conf import settings


logger = logging.getLogger(__name__)

_AUTOSTART_LOCK = threading.Lock()
_AUTOSTART_THREAD_STARTED = False


def _celery_process_pattern():
    return getattr(settings, 'CELERY_PROCESS_PATTERN', r'celery.*YuYuWechatV2_Client')


def _celery_base_command():
    celery_executable = getattr(settings, 'CELERY_EXECUTABLE', 'celery')
    return [celery_executable, '-A', 'YuYuWechatV2_Client']


def start_celery_processes():
    if is_celery_running():
        logger.info('Celery is already running; skip starting another worker/beat pair.')
        return None, None

    env = os.environ.copy()
    env.setdefault('DJANGO_SETTINGS_MODULE', 'YuYuWechatV2_Client.settings')
    popen_kwargs = {
        'cwd': str(settings.BASE_DIR),
        'env': env,
    }

    worker = subprocess.Popen(
        _celery_base_command() + ['worker', '--loglevel=info'],
        **popen_kwargs,
    )
    beat = subprocess.Popen(
        _celery_base_command() + ['beat', '--loglevel=info'],
        **popen_kwargs,
    )
    return worker, beat


def _is_target_celery_command(command):
    return 'celery' in command and 'YuYuWechatV2_Client' in command


def _classify_celery_process(command):
    if ' worker' in f' {command} ':
        return 'worker'
    if ' beat' in f' {command} ':
        return 'beat'
    return 'unknown'


def _parse_ps_output(output):
    if isinstance(output, bytes):
        output = output.decode(errors='replace')

    processes = []
    for line in (output or '').splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue

        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue

        command = parts[2]
        if not _is_target_celery_command(command):
            continue

        processes.append({
            'pid': pid,
            'ppid': ppid,
            'kind': _classify_celery_process(command),
            'command': command,
        })
    return processes


def get_celery_process_report():
    """
    Count Celery worker/beat root processes for this project.

    Celery prefork workers create child processes, so counting every matching
    process would produce false alarms. Root process counting flags duplicate
    worker/beat instances while ignoring worker pool children.
    """
    result = subprocess.run(
        ['ps', '-eo', 'pid=,ppid=,args='],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    processes = _parse_ps_output(result.stdout)
    process_pids = {process['pid'] for process in processes}
    root_processes = [
        process for process in processes
        if process['ppid'] not in process_pids
    ]

    worker_count = sum(1 for process in root_processes if process['kind'] == 'worker')
    beat_count = sum(1 for process in root_processes if process['kind'] == 'beat')
    unknown_count = sum(1 for process in root_processes if process['kind'] == 'unknown')
    duplicate = worker_count > 1 or beat_count > 1 or unknown_count > 0

    return {
        'running': bool(root_processes),
        'process_count': len(root_processes),
        'worker_count': worker_count,
        'beat_count': beat_count,
        'unknown_count': unknown_count,
        'duplicate': duplicate,
        'processes': root_processes,
    }


def stop_celery_processes():
    result = subprocess.run(
        ['pkill', '-f', _celery_process_pattern()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode in (0, 1)


def is_celery_running():
    return get_celery_process_report()['running']


def should_schedule_celery_autostart(argv=None, env=None):
    argv = argv or sys.argv
    env = env or os.environ

    if not getattr(settings, 'AUTO_START_CELERY_ON_WEB_START', True):
        return False

    if len(argv) < 2 or argv[1] != 'runserver':
        return False

    # Django 默认开启自动重载时，父进程也会加载应用；只在真正提供服务的子进程中执行。
    if '--noreload' not in argv and env.get('RUN_MAIN') != 'true':
        return False

    return True


def _delayed_restart_celery():
    delay_seconds = getattr(settings, 'AUTO_START_CELERY_DELAY_SECONDS', 30)
    stop_wait_seconds = getattr(settings, 'AUTO_START_CELERY_STOP_WAIT_SECONDS', 2)

    try:
        delay_seconds = max(int(delay_seconds), 0)
    except (TypeError, ValueError):
        delay_seconds = 30

    try:
        stop_wait_seconds = max(int(stop_wait_seconds), 0)
    except (TypeError, ValueError):
        stop_wait_seconds = 2

    if delay_seconds:
        time.sleep(delay_seconds)

    logger.info('Restarting Celery processes after web startup.')
    stop_celery_processes()

    if stop_wait_seconds:
        time.sleep(stop_wait_seconds)

    try:
        start_celery_processes()
    except Exception:
        logger.exception('Failed to auto-start Celery processes.')


def schedule_celery_autostart():
    global _AUTOSTART_THREAD_STARTED

    if not should_schedule_celery_autostart():
        return False

    with _AUTOSTART_LOCK:
        if _AUTOSTART_THREAD_STARTED:
            return False

        thread = threading.Thread(
            target=_delayed_restart_celery,
            name='celery-autostart',
            daemon=True,
        )
        thread.start()
        _AUTOSTART_THREAD_STARTED = True
        return True
