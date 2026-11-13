#!/usr/bin/env python3
"""Detecta cambios incompatibles en los esquemas de eventos.

Compara eventos/esquemas/*.json contra la rama base (por defecto origin/main).
Uso: python scripts/comparar_esquemas.py [rama_base]

Se permite: cambiar descripciones y agregar campos opcionales o eventos nuevos.
Se bloquea: borrar un esquema o un campo, cambiar tipos, restricciones o
campos obligatorios. Si el cambio es intencional, crea una versión nueva
(...v2.json) en lugar de editar la v1.
"""
import json
import subprocess
import sys
from pathlib import Path

DIR = 'eventos/esquemas'
CLAVES = ('$schema', 'type', 'pattern', 'minimum', 'maximum', 'exclusiveMinimum',
          'exclusiveMaximum', 'minLength', 'maxLength', 'minItems', 'maxItems',
          'format', 'enum', 'additionalProperties')


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True,
                          check=True).stdout


def normalizar(clave, valor):
    if clave == 'enum' and isinstance(valor, list):
        return sorted(valor, key=json.dumps)
    return valor


def comparar(viejo, nuevo, ruta, errores):
    if not isinstance(viejo, dict) or not isinstance(nuevo, dict):
        return
    for clave in CLAVES:
        a, b = normalizar(clave, viejo.get(clave)), normalizar(clave, nuevo.get(clave))
        if a != b:
            errores.append(f'{ruta}: cambió "{clave}" ({a} -> {b})')

    req_viejo, req_nuevo = set(viejo.get('required', [])), set(nuevo.get('required', []))
    for campo in sorted(req_nuevo - req_viejo):
        errores.append(f'{ruta}: "{campo}" ahora es obligatorio')
    for campo in sorted(req_viejo - req_nuevo):
        errores.append(f'{ruta}: "{campo}" dejó de ser obligatorio')

    props_viejas = viejo.get('properties', {})
    props_nuevas = nuevo.get('properties', {})
    for nombre, definicion in props_viejas.items():
        if nombre not in props_nuevas:
            errores.append(f'{ruta}: se eliminó el campo "{nombre}"')
        else:
            comparar(definicion, props_nuevas[nombre], f'{ruta}.{nombre}', errores)

    if 'items' in viejo:
        comparar(viejo['items'], nuevo.get('items', {}), f'{ruta}[]', errores)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'origin/main'
    try:
        archivos = git('ls-tree', '-r', '--name-only', base, DIR + '/').splitlines()
    except subprocess.CalledProcessError:
        print(f'No pude leer la rama base "{base}". ¿Hiciste git fetch?')
        return 2

    errores = []
    for ruta in archivos:
        if not ruta.endswith('.json') or Path(ruta).name.startswith('_'):
            continue
        actual = Path(ruta)
        if not actual.exists():
            errores.append(f'{Path(ruta).stem}: el esquema fue eliminado')
            continue
        viejo = json.loads(git('show', f'{base}:{ruta}'))
        nuevo = json.loads(actual.read_text(encoding='utf-8'))
        comparar(viejo, nuevo, Path(ruta).stem, errores)

    if errores:
        print('CAMBIOS INCOMPATIBLES EN ESQUEMAS DE EVENTOS:\n')
        for e in errores:
            print(f'  - {e}')
        print('\nSi el cambio es intencional, crea una versión nueva del evento '
              '(por ejemplo ...v2.json) en vez de editar la v1.')
        return 1

    print(f'OK: {len(archivos)} esquemas revisados, sin cambios incompatibles.')
    return 0


if __name__ == '__main__':
    sys.exit(main())