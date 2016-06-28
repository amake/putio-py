'''
Created on 2012/10/08

@author: amake
'''

import os
import shutil
import json
import urllib
import re
import logging

API = 'https://api.put.io/v2/{0}?oauth_token={1}&'
CONFIG = {}
GLOBAL_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'putioconf.json')
USER_CONFIG_FILE = os.path.expanduser(os.path.join('~', '.putio'))
for config_file in (GLOBAL_CONFIG_FILE, USER_CONFIG_FILE):
    if os.path.exists(config_file):
        with open(config_file) as temp:
            CONFIG.update(json.load(temp))
TOKEN = CONFIG['token']
CALLBACK_URL = CONFIG['callback_url']
REPLACEMENTS = CONFIG.get('replacements', None)
REPLACEMENTS_COMPILED = None

TARGET_FOLDER = os.path.expanduser('~/Desktop')

DEBUG = False

STATUS_COMPLETED = 'COMPLETED'
STATUS_COMPLETING = 'COMPLETING'
STATUS_DOWNLOADING = 'DOWNLOADING'

TYPE_DIRECTORY = 'application/x-directory'
TYPE_VIDEO = 'video'

INSTANCE = None


def do_replacements(text):
    global REPLACEMENTS_COMPILED
    if REPLACEMENTS == None:
        return text
    if REPLACEMENTS_COMPILED == None:
        REPLACEMENTS_COMPILED = []
        for search, replace in REPLACEMENTS.items():
            try:
                REPLACEMENTS_COMPILED.append((re.compile(search), replace))
            except re.error:
                logging.info('Bad search regex: %s', search)
    for search, replace in REPLACEMENTS_COMPILED:
        text = search.sub(replace, text)
    return text


def get_api(token=TOKEN, target_folder=TARGET_FOLDER):
    global INSTANCE
    if INSTANCE == None:
        INSTANCE = Api(token=token, target_folder=target_folder)

    return INSTANCE

class Api:

    MAX_TRIES = 5

    def __init__(self, token=TOKEN, target_folder=TARGET_FOLDER):
        self.__token__ = token
        self.__target_folder__ = target_folder

    def __api__(self, command, get_param=''):
        return API.format(command, self.__token__) + get_param

    def __call__(self, command, data=None, get_param=''):
        url = self.__api__(command, get_param)
        response = urllib.urlopen(url, data=data).read()
        try:
            return json.loads(response)
        except Exception, e:
            raise Exception('Could not parse response:\n' + response[:100]
                            + '... (truncated)' if len(response) > 100 else '')

    def flush(self):
        for transfer in self.transfers():
            if transfer['status'] not in (STATUS_COMPLETED,
                                          STATUS_COMPLETING,
                                          STATUS_DOWNLOADING):
                self.cancel(transfer['id'])
                print 'Killed zombie transfer.'

    def files(self, parent_id=0):
        print 'Fetching file list for dir {0}...'.format(parent_id),
        response = self.__call__('files/list',
                                 get_param='parent_id=' + str(parent_id))
        print 'done'
        if 'files' not in response:
            raise Exception('Failed to query files')
        files = response['files']
        logging.debug('Available files (%d total):', len(files))
        for a_file in files:
            logging.debug('  %s', a_file['name'])
        return files

    def transfers(self):
        logging.info('Fetching transfer list ...')
        response = self.__call__('transfers/list')
        logging.info('done')
        if 'transfers' not in response:
            raise Exception('Failed to query transfers')
        transfers = response['transfers']
        logging.debug('Current transfers (%d total):', len(transfers))
        for transfer in transfers:
            logging.debug('  %s', transfer['name'])
        return transfers

    def transfer_status(self, transfer_id):
        return self.transfer(transfer_id)['status']

    def transfer(self, transfer_id):
        logging.info('Getting status of transfer %s ...', transfer_id)
        response = self.__call__('transfers/{0}'.format(transfer_id))
        logging.info('done')
        return response['transfer']

    def file(self, file_id):
        logging.info('Getting info for file ID %s...', file_id)
        response = self.__call__('files/{0}'.format(file_id))
        logging.info('done')
        if 'file' not in response:
            raise Exception('Failed to query file')
        return response['file']

    def download(self, file_id, target_dir):
        results = []
        file_info = self.file(file_id)
        if file_info['content_type'] == TYPE_DIRECTORY:
            results.extend(self.download_dir(file_id, target_dir if str(file_id) == "0" \
                                             else os.path.join(target_dir,
                                                               file_info['name'])))
        else:
            results.append(self.download_file(file_info, target_dir))
        return results

    def download_file(self, file_info, target_dir):
        file_id = file_info['id']
        name = do_replacements(file_info['name'])
        logging.info('Downloading %s (ID: %s) to %s ...', 
            name,
            file_id,
            target_dir)
        url = self.__api__('files/{0}/download'.format(file_id))
        temp_path = None
        for n in range(self.MAX_TRIES):
            while True:
                try:
                    temp_path, headers = urllib.urlretrieve(url)
                except urllib.ContentTooShortError:
                    logging.warning('File was not the right size. Retrying.')
                else:
                    break
            if os.path.getsize(temp_path) == file_info['size']:
                break
            logging.error('Error: Unexpected file size. '
                          'Was: %d; expected: %d. Trying again.',
                          os.path.getsize(temp_path),
                          file_info['size'])
            os.remove(temp_path)
        else:
            raise Exception('Failed to download file within %s tries.' %
                            self.MAX_TRIES)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        os.chmod(temp_path, 0777)
        shutil.move(temp_path, os.path.join(target_dir, name))
        logging.info('done')
        return file_id

    def download_dir(self, source_folder, target_folder):
        results = []
        for a_file in self.files(source_folder):
            if a_file['content_type'] == TYPE_DIRECTORY:
                results.extend(self.download_dir(a_file['id'],
                                                 os.path.join(target_folder,
                                                              a_file['name'])))
            else:
                results.append(self.download_file(a_file, target_folder))
        return results if str(source_folder) == '0' else [source_folder]

    def delete(self, file_id):
        logging.info('Deleting %s ...', file_id)
        data = urllib.urlencode({'file_ids': file_id})
        response = self.__call__('files/delete', data)
        if response['status'] != 'OK':
            raise Exception('Failed to delete file ID: ' + file_id)
        logging.info('done')

    def cancel(self, transfer_id):
        logging.info('Canceling %s ...', transfer_id)
        data = urllib.urlencode({'transfer_ids': transfer_id})
        response = self.__call__('transfers/cancel', data)
        if response['status'] != 'OK':
            raise Exception('Failed to cancel transfer ID: ' + transfer_id)
        logging.info('done')

    def add(self, link, callback=CALLBACK_URL):
        logging.info('Adding transfer ...')
        data = urllib.urlencode({'url': link,
                                 'save_parent_id': 0,
                                 'extract': False,
                                 'callback_url': callback})
        response = self.__call__('transfers/add', data)
        if response['status'] != 'OK':
            raise Exception('Error: ' + response['error_message'])
        logging.info('done')
        return response['transfer']['id']

if __name__ == '__main__':
    pass
