from __future__ import annotations

import json
from pathlib import Path

from app.main import app

spec = app.openapi()
items = []
for path, path_item in sorted(spec['paths'].items()):
    for method, operation in path_item.items():
        if method not in {'get', 'post', 'put', 'patch', 'delete'}:
            continue
        url_path = path
        for parameter in operation.get('parameters', []):
            if parameter.get('in') == 'path':
                name = parameter['name']
                url_path = url_path.replace('{' + name + '}', '{{' + name + '}}')
        request = {
            'method': method.upper(),
            'header': [
                {'key': 'Authorization', 'value': 'Bearer {{access_token}}', 'type': 'text'},
                {'key': 'Content-Type', 'value': 'application/json', 'type': 'text'},
            ],
            'url': {'raw': '{{base_url}}' + url_path, 'host': ['{{base_url}}'], 'path': [p for p in url_path.split('/') if p]},
        }
        body = operation.get('requestBody', {}).get('content', {}).get('application/json', {})
        if body:
            request['body'] = {'mode': 'raw', 'raw': '{}', 'options': {'raw': {'language': 'json'}}}
        items.append({'name': operation.get('summary') or operation.get('operationId'), 'request': request})

collection = {
    'info': {
        'name': 'Enterprise Authentication Service v3',
        '_postman_id': 'f91cbb02-e1bb-41b2-a783-enterpriseauthv3',
        'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
    },
    'variable': [
        {'key': 'base_url', 'value': 'http://localhost:8000'},
        {'key': 'access_token', 'value': ''},
        {'key': 'organization_id', 'value': ''},
        {'key': 'user_id', 'value': ''},
        {'key': 'role_id', 'value': ''},
    ],
    'item': items,
}
Path('postman_collection.json').write_text(json.dumps(collection, indent=2), encoding='utf-8')
print('Wrote postman_collection.json')
