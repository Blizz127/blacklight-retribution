#!/usr/bin/env python3
"""Option B: build the create body for the new blrevive container from the OLD container's saved inspect.
Copies Config/HostConfig verbatim and changes only: Entrypoint/Cmd (wrapper), Image (pinned ID), compose labels
(dropped), Hostname (docker default), the anonymous assets volume (re-attached BY NAME) and the new ro wrapper bind.

  recreate.py <old-inspect.json>              dry run: prints the body with Env values redacted
  recreate.py <old-inspect.json> --body OUT   writes the full body (0600) for POST /containers/create?name=blrevive
  recreate.py --diff <old-inspect.json> <new-inspect.json>   prints every differing field (Env values redacted)
"""
import json
import os
import sys

IMAGE = 'sha256:97f9be01eb41'
OLD_ID = '7fad6a7a8e68fa4ef91b81a97b72d7bdc3ec9c2d687753d4b8443fbd72b38212'
ASSETS = '/mnt/blacklightre/blre'
WRAPPER_HOST = '/home/blizz/servers/blrevive-sleepwake/optionb/blr-init.sh'
WRAPPER = '/opt/blr-init/blr-init.sh'
ENTRYPOINT, CMD = ['/bin/sh', WRAPPER], ['start']


def body(old):
    assert old['Id'] == OLD_ID, 'not the pinned container'
    cfg = json.loads(json.dumps(old['Config']))
    host = json.loads(json.dumps(old['HostConfig']))
    full_image = old['Image']
    assert full_image.startswith(IMAGE), full_image
    vols = [m for m in old['Mounts'] if m['Type'] == 'volume']
    assert len(vols) == 1 and vols[0]['Destination'] == ASSETS, vols
    cfg.update(Image=full_image, Entrypoint=ENTRYPOINT, Cmd=CMD)
    cfg.pop('Hostname', None)
    cfg['Labels'] = {k: v for k, v in cfg['Labels'].items() if not k.startswith('com.docker.compose.')}
    host['Binds'] = list(host['Binds']) + ['%s:%s:rw' % (vols[0]['Name'], ASSETS)]
    host['Mounts'] = list(host.get('Mounts') or []) + [
        {'Type': 'bind', 'Source': WRAPPER_HOST, 'Target': WRAPPER, 'ReadOnly': True}]
    nets = old['NetworkSettings']['Networks']
    assert list(nets) == ['blrevive_default'], list(nets)
    cfg['HostConfig'] = host
    cfg['NetworkingConfig'] = {'EndpointsConfig': {'blrevive_default': {'Aliases': ['blrevive']}}}
    return cfg


def redact(o):
    o = json.loads(json.dumps(o))
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == 'Env' and isinstance(v, list):
                    x[k] = [e.split('=', 1)[0] + '=<redacted>' for e in v]
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(o)
    return o


def flat(o, p=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flat(v, p + '.' + k)
    else:
        yield p, o


def diff(old, new):
    a, b = dict(flat(old)), dict(flat(new))
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            red = k.endswith('.Env')
            print('%s\n   old: %s\n   new: %s' % (k, '<env>' if red else json.dumps(a.get(k))[:300],
                                                 '<env>' if red else json.dumps(b.get(k))[:300]))
    if a.get('.Config.Env') != b.get('.Config.Env'):
        print('ENV DIFFERS (values redacted)')


if __name__ == '__main__':
    if sys.argv[1] == '--diff':
        diff(json.load(open(sys.argv[2]))[0], json.load(open(sys.argv[3]))[0])
        raise SystemExit(0)
    bd = body(json.load(open(sys.argv[1]))[0])
    if len(sys.argv) > 3 and sys.argv[2] == '--body':
        fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(bd, f)
        print('wrote', sys.argv[3])
    else:
        print(json.dumps(redact(bd), indent=1, sort_keys=True))
