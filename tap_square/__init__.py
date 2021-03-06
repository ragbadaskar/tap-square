#!/usr/bin/env python3

import datetime
import sys

import requests
import singer

from tap_square import utils


REQUIRED_CONFIG_KEYS = ['accessToken', 'start_date']
LIMIT = 200
BASE_URL = "https://connect.squareup.com/v1"
CONFIG = {}
STATE = {}

endpoints = {
    "locations": "/me/locations",
    "payments": "/{location_id}/payments",
    # "tickets": "/api/v2/tickets",
    # "sub_ticket": "/api/v2/tickets/{id}/{entity}",
    # "agents": "/api/v2/agents",
    # "roles": "/api/v2/roles",
    # "groups": "/api/v2/groups",
    # "companies": "/api/v2/companies",
    # "contacts": "/api/v2/contacts",
}

logger = singer.get_logger()
session = requests.Session()


def get_url(endpoint, **kwargs):
    return BASE_URL + endpoints[endpoint].format(**kwargs)


def get_start(entity):
    if entity not in STATE:
        STATE[entity] = CONFIG['start_date']

    return STATE[entity]


def gen_request(url, params=None):
    params = params or {}
    headers = {"Content-Type": "application/json",
               "Accept": "application/json",
               "Authorization": "Bearer {}".format(CONFIG['accessToken'])}
    while True:
        req = requests.Request('GET', url, params=params, headers=headers).prepare()
        logger.info("GET {}".format(req.url))
        resp = session.send(req)

        if resp.status_code >= 400:
            logger.error("GET {} [{} - {}]".format(req.url, resp.status_code, resp.content))
            sys.exit(1)

        data = resp.json()

        for row in data:
            yield row

        if 'next' in resp.links:
            url = resp.links['next']['url']
        else:
            break


def transform_dict(d, key_key="name", value_key="value"):
    return [{key_key: k, value_key: v} for k, v in d.items()]


def sync_tickets():
    singer.write_schema("tickets", utils.load_schema("tickets"), ["id"])
    singer.write_schema("conversations", utils.load_schema("conversations"), ["id"])
    singer.write_schema("satisfaction_ratings", utils.load_schema("satisfaction_ratings"), ["id"])
    singer.write_schema("time_entries", utils.load_schema("time_entries"), ["id"])

    start = get_start("tickets")
    params = {
        'updated_since': start,
        'order_by': "updated_at",
        'order_type': "asc",
    }
    for row in gen_request(get_url("tickets"), params):
        logger.info("Ticket {}: Syncing".format(row['id']))
        row.pop('attachments', None)
        row['custom_fields'] = transform_dict(row['custom_fields'])

        # get all sub-entities and save them
        logger.info("Ticket {}: Syncing conversations".format(row['id']))
        for subrow in gen_request(get_url("sub_ticket", id=row['id'], entity="conversations")):
            subrow.pop("attachments", None)
            subrow.pop("body", None)
            if subrow['updated_at'] >= start:
                singer.write_record("conversations", subrow)

        logger.info("Ticket {}: Syncing satisfaction ratings".format(row['id']))
        for subrow in gen_request(get_url("sub_ticket", id=row['id'], entity="satisfaction_ratings")):
            subrow['ratings'] = transform_dict(subrow['ratings'], key_key="question")
            if subrow['updated_at'] >= start:
                singer.write_record("satisfaction_ratings", subrow)

        logger.info("Ticket {}: Syncing time entries".format(row['id']))
        for subrow in gen_request(get_url("sub_ticket", id=row['id'], entity="time_entries")):
            if subrow['updated_at'] >= start:
                singer.write_record("time_entries", subrow)

        utils.update_state(STATE, "tickets", row['updated_at'])
        singer.write_record("tickets", row)
        singer.write_state(STATE)


def sync_time_filtered(entity):
    singer.write_schema(entity, utils.load_schema(entity), ["id"])
    start = get_start(entity)

    logger.info("Syncing {} from {}".format(entity, start))
    for row in gen_request(get_url(entity)):
        if row['updated_at'] >= start:
            if 'custom_fields' in row:
                row['custom_fields'] = transform_dict(row['custom_fields'])

            utils.update_state(STATE, entity, row['updated_at'])
            singer.write_record(entity, row)

    singer.write_state(STATE)

# TODO: pagination for payments
def sync_payments(location_id):
    logger.info("Location {}: Syncing payments".format(location_id))
    state_name = "payments.{}".format(location_id)
    start = get_start(state_name)
    params = {
        "limit": LIMIT,
        "order": "ASC",
        "begin_time": start
    }
    for payment in gen_request(get_url("payments", location_id=location_id), params):
        if payment['created_at'] >= start:
            singer.write_record("square_payments", payment)
            utils.update_state(STATE, state_name, payment['created_at'])
            singer.write_state(STATE)
        

def sync_locations():
    singer.write_schema("square_location", {}, ["id"])
    singer.write_schema("square_payments", utils.load_schema("payments"), ["id"])
    for location in gen_request(get_url("locations"), {}):
        logger.info("Location {}: Syncing".format(location['id']))
        singer.write_record("square_location", location)

        sync_payments(location['id'])

def do_sync():
    logger.info("Starting Square sync")

    sync_locations()
    # sync_tickets()
    # sync_time_filtered("agents")
    # sync_time_filtered("roles")
    # sync_time_filtered("groups")
    # sync_time_filtered("contacts")
    # sync_time_filtered("companies")

    logger.info("Completed sync")


def main():
    config, state = utils.parse_args(REQUIRED_CONFIG_KEYS)
    CONFIG.update(config)
    STATE.update(state)
    do_sync()


if __name__ == '__main__':
    main()
