#!/usr/bin/env python

# Example of exporting registrations, members, and transactions with batched
# results.  A limited number of results are returned in each response.  It can
# vary based on the type, but is generally around 1000 records.

# install dependencies with: python -m pip install -r requirements.txt

import argparse
import time
import random

import jwt
import requests

parser = argparse.ArgumentParser()
parser.add_argument('--site-id', type=int, required=True)
parser.add_argument('--client-id', required=True,
                    help='client id for site.  Probably the same as the certificate filename basename')
parser.add_argument('--pem-file', required=True, help='filename for certificate key in PEM format')
parser.add_argument('--type', required=True,
                    choices=['registrations-2', 'members-2', 'transactions-2', 'accountingCodes'],
                    help='type of records to export')
parser.add_argument('--domain', default='leagueapps.io')
parser.add_argument('--auth', default='https://auth.leagueapps.io')
args = parser.parse_args()

if args.auth:
    print("using auth server {}".format(args.auth))
    auth_host = args.auth
else:
    auth_host = 'https://auth.leagueapps.io'

if args.domain == 'lapps-local.io':
    # for local testing the Google ESP isn't HTTPS
    admin_host = 'http://admin.{}:8082'.format(args.domain)
else:
    admin_host = 'https://admin.{}'.format(args.domain)

site_id = args.site_id
record_type = args.type


# Make a request to the OAuth 2 token endpoint with a JWT assertion to get an
# access_token
def request_access_token(auth_host_url, client_id, pem_file):
    with open(pem_file, 'r') as f:
        key = f.read()

    now = int(time.time())
    auth_url = '{}/v2/auth/token'.format(auth_host_url)

    claims = {
        'aud': 'https://auth.leagueapps.io/v2/auth/token',
        'iss': client_id,
        'sub': client_id,
        'iat': now,
        'exp': now + 300
    }

    assertion = jwt.encode(claims, key, algorithm='RS256')

    resp = requests.post(auth_url,
                         data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                               'assertion': assertion})

    if resp.status_code == 200:
        return resp.json()['access_token']
    else:
        print('failed to get access_token: ({}) {}'.format(resp.status_code, resp.text))
        return None


# Calculate seconds to sleep between retries.
#
# slot_time is amount of time to for each slot and is multiplied by the slot
# random calculated slot to get the total sleep time.
#
# max_slots can be used to put an upper bound on the sleep time
def exponential_backoff(attempts_so_far, slot_time=1.0, max_slots=0):
    if max_slots > 0:
        attempts_so_far = min(attempts_so_far, max_slots)

    return random.randint(0, 2 ** attempts_so_far - 1) * slot_time


# Initialize the last-updated and last-id query parameters to be used between
# requests.  These should be updated after processing each batch of responses
# to get more results.
last_updated = 0
last_id = 0

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

    if record_type == 'accountingCodes':  # accountingCodes endpoint doesn't have /export/ in it
        url = '{}/v2/sites/{}/{}'.format(admin_host, site_id, record_type)
    else:
        url = '{}/v2/sites/{}/export/{}'.format(admin_host, site_id, record_type)

    try:
        response = requests.get(url, params=params,
                                headers=headers, timeout=10)
    except requests.exceptions.Timeout:
        wait_seconds = exponential_backoff(attempts, 1.42, 5)
        print('retry in {} seconds due to timeout'.format(wait_seconds))
        time.sleep(wait_seconds)
        continue

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
        time.sleep(wait_seconds)
        continue

    # error on request that can't be retried
    if response.status_code != 200:
        print('unexpected error ({}): {}'.format(response.status_code, response.reason))
        # reasonably some sort of coding error and retry is likely to fail
        break

    # get the actual response JSON data
    records = response.json()

    # No more records, exit.
    if len(records) == 0:
        print('done.')
        break

    batch_count += 1

    # successful request, reset retry attempts
    attempts = 0

    # process the result records and do useful things with them
    print('processing batch {}, {} records'.format(batch_count, len(records)))
    printFile = open("records.json", "w+")


    def remove_uni(s):
        s2 = s.replace("u'", "'")
        s2 = s2.replace('u"', '"')
        return s2


    printFile.write("[")
    for record in records:
        # print('record id: {}, {}'.format(record['id'], record['lastUpdated']))
        # track last_updated and last_id so next request will fetch more records
        if record_type != 'accountingCodes':  # accountingCodes endpoint doesn't return this data
            last_updated = record['lastUpdated']
            last_id = record['id']
        printFile.write(remove_uni(str(record)) + ",")

    printFile.write("]")
    printFile.close()

    if record_type == 'accountingCodes':
        break  # accountingCodes endpoint is not paginated, so no need to loop
