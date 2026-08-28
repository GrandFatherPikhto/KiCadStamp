#!/usr/bin/env python3
"""
transform_template.py — преобразование шаблона спицы.
Порядок: сначала перенос начала координат на указанный элемент (via или компонент),
затем поворот и зеркалирование относительно нового начала.
"""
import argparse
import math
from typing import Dict, Any, Optional, Tuple

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict

def load_template(input_path: str) -> Dict[str, Any]:
    with open(input_path, 'r', encoding='utf-8') as f:
        data = sexp_to_dict(f.read()) or {}
    if 'templates' in data:
        name = next(iter(data['templates']))
        return {'name': name, 'template': data['templates'][name]}
    else:
        return {'name': 'template', 'template': data}

def save_template(output_path: str, name: str, template: Dict[str, Any]):
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(dict_to_sexp({'templates': {name: template}}))

def rotate_coords(along: float, across: float, angle_deg: float) -> Tuple[float, float]:
    rad = math.radians(angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    return along*c - across*s, along*s + across*c

def find_element(template: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    typ = spec.get('type')
    if typ == 'via':
        if 'index' in spec:
            idx = spec['index']
            vias = template.get('vias', [])
            if 0 <= idx < len(vias):
                v = vias[idx]
                return v.get('offset_along_mm', 0.0), v.get('offset_across_mm', 0.0)
        if 'net' in spec:
            net = spec['net']
            for v in template.get('vias', []):
                if v.get('net') == net:
                    return v.get('offset_along_mm', 0.0), v.get('offset_across_mm', 0.0)
    elif typ == 'component':
        if 'index' in spec:
            idx = spec['index']
            comps = template.get('components', [])
            if 0 <= idx < len(comps):
                c = comps[idx]
                return c.get('offset_along_mm', 0.0), c.get('offset_across_mm', 0.0)
        if 'role' in spec:
            role = spec['role']
            for c in template.get('components', []):
                if c.get('role') == role:
                    return c.get('offset_along_mm', 0.0), c.get('offset_across_mm', 0.0)
    return None

def apply_transform(template: Dict[str, Any],
                    rotate_deg: float = 0.0,
                    mirror_x: bool = False,
                    mirror_y: bool = False,
                    origin_element: Optional[Dict[str, Any]] = None,
                    origin_x: Optional[float] = None,
                    origin_y: Optional[float] = None) -> Dict[str, Any]:
    # Определяем смещение (origin_along, origin_across) в исходном шаблоне
    if origin_element is not None:
        coords = find_element(template, origin_element)
        if coords is None:
            raise ValueError(f"Элемент не найден: {origin_element}")
        ox, oy = coords
    elif origin_x is not None and origin_y is not None:
        ox, oy = origin_x, origin_y
    else:
        ox, oy = 0.0, 0.0

    # Перенос начала: вычитаем (ox, oy) из всех via и компонентов
    new_template = {'layer': template.get('layer', 'F.Cu'), 'vias': [], 'components': []}
    for v in template.get('vias', []):
        nv = v.copy()
        nv['offset_along_mm'] = round(v['offset_along_mm'] - ox, 6)
        nv['offset_across_mm'] = round(v['offset_across_mm'] - oy, 6)
        new_template['vias'].append(nv)
    for c in template.get('components', []):
        nc = c.copy()
        nc['offset_along_mm'] = round(c['offset_along_mm'] - ox, 6)
        nc['offset_across_mm'] = round(c['offset_across_mm'] - oy, 6)
        nc['angle_deg'] = c.get('angle_deg', 0.0)
        new_template['components'].append(nc)

    # Теперь применяем зеркалирование и поворот к смещённым координатам
    # Vias
    for v in new_template['vias']:
        a, b = v['offset_along_mm'], v['offset_across_mm']
        if mirror_x:
            b = -b
        if mirror_y:
            a = -a
        if rotate_deg:
            a, b = rotate_coords(a, b, rotate_deg)
        v['offset_along_mm'] = round(a, 6)
        v['offset_across_mm'] = round(b, 6)
    # Components
    for c in new_template['components']:
        a, b = c['offset_along_mm'], c['offset_across_mm']
        ang = c['angle_deg']
        if mirror_x:
            b = -b
        if mirror_y:
            a = -a
        if mirror_x or mirror_y:
            ang = -ang
        if rotate_deg:
            a, b = rotate_coords(a, b, rotate_deg)
            ang += rotate_deg
        c['offset_along_mm'] = round(a, 6)
        c['offset_across_mm'] = round(b, 6)
        c['angle_deg'] = round(ang % 360.0, 6)
    return new_template

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', required=True)
    parser.add_argument('-o', '--output', required=True)
    parser.add_argument('--rotate', type=float, default=0.0)
    parser.add_argument('--mirror-x', action='store_true')
    parser.add_argument('--mirror-y', action='store_true')
    parser.add_argument('--set-origin-by-via-index', type=int)
    parser.add_argument('--set-origin-by-via-net', type=str)
    parser.add_argument('--set-origin-by-component-index', type=int)
    parser.add_argument('--set-origin-by-component-role', type=str)
    parser.add_argument('--origin-x', type=float)
    parser.add_argument('--origin-y', type=float)
    args = parser.parse_args()

    data = load_template(args.input)
    name, template = data['name'], data['template']

    origin_element = None
    origin_x = None
    origin_y = None

    if args.set_origin_by_via_index is not None:
        origin_element = {'type': 'via', 'index': args.set_origin_by_via_index}
    elif args.set_origin_by_via_net is not None:
        origin_element = {'type': 'via', 'net': args.set_origin_by_via_net}
    elif args.set_origin_by_component_index is not None:
        origin_element = {'type': 'component', 'index': args.set_origin_by_component_index}
    elif args.set_origin_by_component_role is not None:
        origin_element = {'type': 'component', 'role': args.set_origin_by_component_role}
    else:
        if args.origin_x is not None:
            origin_x = args.origin_x
        if args.origin_y is not None:
            origin_y = args.origin_y

    new_template = apply_transform(
        template,
        rotate_deg=args.rotate,
        mirror_x=args.mirror_x,
        mirror_y=args.mirror_y,
        origin_element=origin_element,
        origin_x=origin_x,
        origin_y=origin_y
    )
    save_template(args.output, name, new_template)
    print(f"Сохранён: {args.output}")

if __name__ == '__main__':
    main()