#!/usr/bin/env python

# Example of exporting registrations, members, and transactions with batched
# results.  A limited number of results are returned in each response.  It can
# vary based on the type, but is generally around 1000 records.

# ubuntu 16.04: sudo apt install python-jwt python-crypto python-requests
# untested: pip install pyjwt crypto requests2

import argparse
import time
import random

import jwt
import requests
import json

parser = argparse.ArgumentParser()
parser.add_argument('--site-id', type=int, required=True)
parser.add_argument('--client-id', required=True,
                    help='client id for site.  Probably the same as the certificate filename basename')
parser.add_argument('--pem-file', required=True, help='filename for certificate key in PEM format')
parser.add_argument('--type', required=True, choices=['registrations-2', 'members-2', 'transactions-2'],
                    help='type of records to export')
parser.add_argument('--domain', default='leagueapps.io')
parser.add_argument('--auth', default='https://auth.leagueapps.io')
parser.add_argument('--last-updated', type=int, default=0)
parser.add_argument('--last-id', type=int, default=0)
args = parser.parse_args()

if args.auth:
    print("using auth server {}".format(args.auth))
    auth_host = args.auth

if args.domain == 'lapps-local.io':
    # for local testing the Google ESP isn't HTTPS
    admin_host = 'http://admin.{}:8082'.format(args.domain)
else:
    admin_host = 'https://admin.{}'.format(args.domain)

site_id = args.site_id
record_type = args.type


# Make a request to the OAuth 2 token endpoint with a JWT assertion to get an
# access_token
def request_access_token(auth_host, client_id, pem_file):
    with open(pem_file, 'r') as f:
        key = f.read()

    now = int(time.time())

    claims = {
        'aud': 'https://auth.leagueapps.io/v2/auth/token',
        'iss': client_id,
        'sub': client_id,
        'iat': now,
        'exp': now + 300
    }

    assertion = jwt.encode(claims, key, algorithm='RS256')

    auth_url = '{}/v2/auth/token'.format(auth_host)

    response = requests.post(auth_url,
                             data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                                   'assertion': assertion})

    if response.status_code == 200:
        return response.json()['access_token']
    else:
        print('failed to get access_token: ({}) {}'.format(response.status_code, response.text))
        return None


# Calculate seconds to sleep between retries.
#
# slot_time is amount of time to for each slot and is multipled by the slot
# random calculated slot to get the total sleep time.
#
# max_slots can be used to put an upper bound on the sleep time
def exponential_backoff(attempts, slot_time=1, max_slots=0):
    if max_slots > 0:
        attempts = min(attempts, max_slots)

    return random.randint(0, 2 ** attempts - 1) * slot_time


# Initialize the last-updated and last-id query parameters to be used between
# requests.  These should be updated after processing each batch of responses
# to get more results.
last_updated = args.last_updated
last_id = args.last_id

access_token = None
batch_count = 0

# Maximum number of retries for a request
max_attempts = 5
attempts = 0
while attempts < max_attempts:
    attempts += 1

    # Get an access_token if necessary
    if access_token is None:
        print('requesting access token: {} {}'.format(args.client_id, args.pem_file))
        access_token = request_access_token(auth_host, args.client_id, args.pem_file)
        if access_token is None:
            break

    print('access token: {}'.format(access_token))

    params = {'last-updated': last_updated, 'last-id': last_id}
    # set the access token in the request header
    headers = {'authorization': 'Bearer {}'.format(access_token)}

    response = requests.get('{}/v2/sites/{}/export/{}'.format(admin_host, site_id, record_type), params=params,
                            headers=headers)

    # access_token is invalid, clear so next pass through the loop will get a new one
    if response.status_code == 401:
        print('error({}): {}'.format(response.status_code, response.text))
        access_token = None
        # immediately retry since it should get a new access token
        continue

    # Request can be retried, sleep before retrying
    if response.status_code == 429 or response.status_code >= 500:
        # sleep an exponential back-off amount of time
        wait_seconds = exponential_backoff(attempts, 1.42, 5)
        print('retry in {} on error status ({}): {}'.format(wait_seconds, response.status_code, response.reason))
        time.sleep(wait_seconds);
        continue

    # error on request that can't be retried
    if response.status_code != 200:
        print('unexpected error ({}): {}'.format(response.status_code, response.reason))
        # reasonably some sort of coding error and retry is likely to fail
        break

    # get the actual response JSON data
    records = response.json()

    # No more records, exit.
    if (len(records) == 0):
        print('done.')
        break

    batch_count += 1
    with open('./registrations_{}'.format(batch_count)+'.json', 'w+') as file:
        json.dump(records, file)
    # successful request, reset retry attempts
    attempts = 0

    # process the result records and do useful things with them
    print('processing batch {}, {} records'.format(batch_count, len(records)))
    for record in records:
        # print('record id: {}, {}'.format(record['id'], record['lastUpdated']))
        # track last_updated and last_id so next request will fetch more records
        last_updated = record['lastUpdated']
        last_id = record['id']
