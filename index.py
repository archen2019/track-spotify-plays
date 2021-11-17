import boto3
import requests

from base64 import b64decode
import os
import re
import time

refresh_token = None
spotify_client_id = None
spotify_client_secret = None

def get_auth_token():
    global refresh_token
    global spotify_client_id
    global spotify_client_secret

    if refresh_token is None:
        if 'AWS_LAMBDA_FUNCTION_NAME' in os.environ:
            kms = boto3.client('kms')
            refresh_token = kms.decrypt(
                CiphertextBlob=b64decode(os.environ['SPOTIFY_REFRESH_TOKEN']),
                EncryptionContext={'LambdaFunctionName': os.environ['AWS_LAMBDA_FUNCTION_NAME']}
            )['Plaintext'].decode('utf-8')
            spotify_client_id = kms.decrypt(
                CiphertextBlob=b64decode(os.environ['SPOTIFY_CLIENT_ID']),
                EncryptionContext={'LambdaFunctionName': os.environ['AWS_LAMBDA_FUNCTION_NAME']}
            )['Plaintext'].decode('utf-8')
            spotify_client_secret = kms.decrypt(
                CiphertextBlob=b64decode(os.environ['SPOTIFY_CLIENT_SECRET']),
                EncryptionContext={'LambdaFunctionName': os.environ['AWS_LAMBDA_FUNCTION_NAME']}
            )['Plaintext'].decode('utf-8')
        else:
            refresh_token = os.environ['SPOTIFY_REFRESH_TOKEN']
            spotify_client_id = os.environ['SPOTIFY_CLIENT_ID']
            spotify_client_secret = os.environ['SPOTIFY_CLIENT_SECRET']

    response = None
    while response is None:
        response = requests.post('https://accounts.spotify.com/api/token', data=dict(
            client_id=spotify_client_id,
            client_secret=spotify_client_secret,
            refresh_token=refresh_token,
            grant_type='refresh_token',
        ))
        if response.status_code >= 500:
            response = None
            time.sleep(1)
        else:
            response.raise_for_status()

    return response.json()['access_token']

def spotify_response_item_to_db_item(item):
    played_at = item['played_at']
    m = re.match('^(\d{4}-\d{2})-', played_at)

    assert m, 'played_at must look like an ISO date time'

    yyyymm = m.group(1)

    attrs = dict(
        yyyymm=dict(
            S=yyyymm,
        ),
        played_at=dict(
            S=played_at,
        ),
        artist=dict(
            S=item['track']['artists'][0]['name'],
        ),
        album=dict(
            S=item['track']['album']['name'],
        ),
        title=dict(
            S=item['track']['name'],
        ),
        track_id=dict(
            S=item['track']['id'],
        ),
    )

    if item['context'] is not None:
        attrs['context_type'] = dict(
            S=item['context']['type'],
        )
        attrs['context_uri'] = dict(
            S=item['context']['uri'],
        )

    return dict(
        PutRequest=dict(
            Item=attrs,
        )
    )

def handler(event, context):
    db = boto3.client('dynamodb')
    auth_token = get_auth_token()
    url = 'https://api.spotify.com/v1/me/player/recently-played'
    while url is not None:
        res = None
        while res is None:
            res = requests.get(url,
                headers=dict(
                    Authorization=f'Bearer {auth_token}',
                ))
            if res.status_code >= 500:
                res = None
                time.sleep(1)
            else:
                res.raise_for_status()
        print('Status:', res.status_code)
        result = res.json()
        # XXX make sure your batches aren't too big
        db_items = [ spotify_response_item_to_db_item(item) for item in result['items'] ]
        if db_items:
            for db_item in db_items:
                print('Adding:', db_item)
            db.batch_write_item(
                RequestItems={
                    'spotify-plays': db_items,
                },
            )
        url = result.get('next')
