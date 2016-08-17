#-*- coding: utf-8 -*-

"""
 (c) 2014 - Copyright Red Hat Inc

 Authors:
   Pierre-Yves Chibon <pingou@pingoured.fr>

License: GPLv3 or any later version.
"""

import argparse
import ConfigParser
import datetime
import glob
import logging
import os
import rpm
import subprocess
import shutil
import time
import warnings
from datetime import date

import requests
try:
    import pygit2
except ImportError:
    pass
try:
    import hglib
except ImportError:
    pass


DEFAULT_CONFIG = os.path.expanduser('~/.config/dgroc')
COPR_URL = 'http://copr.fedoraproject.org/'
# Initial simple logging stuff
logging.basicConfig(format='%(message)s')
LOG = logging.getLogger("dgroc")


class DgrocException(Exception):
    ''' Exception specific to dgroc so that we will catch, we won't catch
    other.
    '''
    pass


class GitReader(object):
    '''Defualt version control system to use: git'''
    short = 'git'

    @classmethod
    def init(cls):
        '''Import the stuff git needs again and let it raise an exception now'''
        import pygit2

    @classmethod
    def clone(cls, url, folder):
        '''Clone the repository'''
        pygit2.clone_repository(url, folder)

    @classmethod
    def pull(cls):
        '''Pull from the repository'''
        return subprocess.Popen(["git", "pull"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @classmethod
    def commit_hash(cls, folder):
        '''Get the latest commit hash'''
        repo = pygit2.Repository(folder)
        commit = repo[repo.head.target]
        return commit.oid.hex[:8]

    @classmethod
    def archive_cmd(cls, project, archive_name):
        '''Command to generate the archive'''
        return ["git", "archive", "--format=tar", "--prefix=%s/" % project,
           "-o%s/%s" % (get_rpm_sourcedir(), archive_name), "HEAD"]

class MercurialReader(object):
    '''Alternative version control system to use: hg'''
    short = 'hg'

    @classmethod
    def init(cls):
        '''Import the stuff Mercurial needs again and let it raise an exception now'''
        import hglib

    @classmethod
    def clone(cls, url, folder):
        '''Clone the repository'''
        hglib.clone(url, folder)

    @classmethod
    def pull(cls):
        '''Pull from the repository'''
        return subprocess.Popen(["hg", "pull"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @classmethod
    def commit_hash(cls, folder):
        '''Get the latest commit hash'''
        repo = hglib.open(folder)
        commit = commit = repo.log('tip')[0]
        return commit.node[:12]

    @classmethod
    def archive_cmd(cls, project, archive_name):
        '''Command to generate the archive'''
        return ["hg", "archive", "--type=tar", "--prefix=%s/" % project,
           "%s/%s" % (get_rpm_sourcedir(), archive_name)]


def _get_copr_auth():
    ''' Return the username, login and API token from the copr configuration
    file.
    '''
    LOG.debug('Reading configuration for copr')
    ## Copr config check
    copr_config_file = os.path.expanduser('~/.config/copr')
    if not os.path.exists(copr_config_file):
        raise DgrocException('No `~/.config/copr` file found.')

    copr_config = ConfigParser.ConfigParser()
    copr_config.read(copr_config_file)

    if not copr_config.has_option('copr-cli', 'username'):
        raise DgrocException(
            'No `username` specified in the `copr-cli` section of the copr '
            'configuration file.')
    username = copr_config.get('copr-cli', 'username')

    if not copr_config.has_option('copr-cli', 'login'):
        raise DgrocException(
            'No `login` specified in the `copr-cli` section of the copr '
            'configuration file.')
    login = copr_config.get('copr-cli', 'login')

    if not copr_config.has_option('copr-cli', 'token'):
        raise DgrocException(
            'No `token` specified in the `copr-cli` section of the copr '
            'configuration file.')
    token = copr_config.get('copr-cli', 'token')

    return (username, login, token)


def get_arguments():
    ''' Set the command line parser and retrieve the arguments provided
    by the command line.
    '''
    parser = argparse.ArgumentParser(
        description='Daily Git Rebuild On Copr')
    parser.add_argument(
        '--config', dest='config', default=DEFAULT_CONFIG,
        help='Configuration file to use for dgroc.')
    parser.add_argument(
        '--debug', dest='debug', action='store_true',
        default=False,
        help='Expand the level of data returned')
    parser.add_argument(
        '--srpm-only', dest='srpmonly', action='store_true',
        default=False,
        help='Generate the new source rpm but do not build on copr')
    parser.add_argument(
        '--no-monitoring', dest='monitoring', action='store_false',
        default=True,
        help='Upload the srpm to copr and exit (do not monitor the build)')

    return parser.parse_args()


def update_spec(spec_file, commit_hash, archive_name, packager, email, reader):
    ''' Update the release tag and changelog of the specified spec file
    to work with the specified commit_hash.
    '''
    LOG.debug('Update spec file: %s', spec_file)
    release = '%s%s%s' % (date.today().strftime('%Y%m%d'), reader.short, commit_hash)
    output = []
    version = None
    rpm.spec(spec_file)
    with open(spec_file) as stream:
        for row in stream:
            row = row.rstrip()
            if row.startswith('Version:'):
                version = row.split('Version:')[1].strip()
            if row.startswith('Release:'):
                if commit_hash in row:
                    raise DgrocException('Spec already up to date')
                LOG.debug('Release line before: %s', row)
                rel_num = row.split('ase:')[1].strip().split('%{?dist')[0]
                rel_list = rel_num.split('.')
                if reader.short in rel_list[-1]:
                    rel_list = rel_list[:-1]
                if rel_list[-1].isdigit():
                    rel_list[-1] = str(int(rel_list[-1])+1)
                rel_num = '.'.join(rel_list)
                LOG.debug('Release number: %s', rel_num)
                row = 'Release:        %s.%s%%{?dist}' % (rel_num, release)
                LOG.debug('Release line after: %s', row)
            if row.startswith('Source0:'):
                row = 'Source0:        %s' % (archive_name)
                LOG.debug('Source0 line after: %s', row)
            if row.startswith('%changelog'):
                output.append(row)
                output.append(rpm.expandMacro('* %s %s <%s> - %s-%s.%s' % (
                    date.today().strftime('%a %b %d %Y'), packager, email,
                    version, rel_num, release)
                ))
                output.append('- Update to %s: %s' % (reader.short, commit_hash))
                row = ''
            output.append(row)

    with open(spec_file, 'w') as stream:
        for row in output:
            stream.write(row + '\n')

    LOG.info('Spec file updated: %s', spec_file)


def get_rpm_sourcedir():
    ''' Retrieve the _sourcedir for rpm
    '''
    dirname = subprocess.Popen(
        ['rpm', '-E', '%_sourcedir'],
        stdout=subprocess.PIPE
    ).stdout.read()[:-1]
    return dirname


def generate_new_srpm(config, project, first=True):
    ''' For a given project in the configuration file generate a new srpm
    if it is possible.
    '''
    if not config.has_option(project, 'scm') or config.get(project, 'scm') == 'git':
        reader = GitReader
    elif config.get(project, 'scm') == 'hg':
        reader = MercurialReader
    else:
        raise DgrocException(
            'Project "%s" tries to use unknown "scm" option'
            % project)
    reader.init()
    LOG.debug('Generating new source rpm for project: %s', project)
    if not config.has_option(project, '%s_folder' % reader.short):
        raise DgrocException(
            'Project "%s" does not specify a "%s_folder" option'
            % (project, reader.short))

    if not config.has_option(project, '%s_url' % reader.short) and not os.path.exists(
            config.get(project, '%s_folder' % reader.short)):
        raise DgrocException(
            'Project "%s" does not specify a "%s_url" option and its '
            '"%s_folder" option does not exists' % (project, reader.short, reader.short))

    if not config.has_option(project, 'spec_file'):
        raise DgrocException(
            'Project "%s" does not specify a "spec_file" option'
            % project)

    # git clone if needed
    git_folder = config.get(project, '%s_folder' % reader.short)
    if '~' in git_folder:
        git_folder = os.path.expanduser(git_folder)

    if not os.path.exists(git_folder):
        git_url = config.get(project, '%s_url' % reader.short)
        LOG.info('Cloning %s', git_url)
        reader.clone(git_url, git_folder)

    # git pull
    cwd = os.getcwd()
    os.chdir(git_folder)
    pull = reader.pull()
    out = pull.communicate()
    os.chdir(cwd)
    if pull.returncode:
        LOG.info('Strange result of the %s pull:\n%s', reader.short, out[0])
        if first:
            LOG.info('Gonna try to re-clone the project')
            shutil.rmtree(git_folder)
            generate_new_srpm(config, project, first=False)
        return

    # Retrieve last commit
    commit_hash = reader.commit_hash(git_folder)
    LOG.info('Last commit: %s', commit_hash)

    # Check if commit changed
    changed = False
    if not config.has_option(project, '%s_hash' % reader.short):
        config.set(project, '%s_hash  % reader.short', commit_hash)
        changed = True
    elif config.get(project, '%s_hash' % reader.short) == commit_hash:
        changed = False
    elif config.get(project, '%s_hash  % reader.short') != commit_hash:
        changed = True

    if not changed:
        return

    # Build sources
    cwd = os.getcwd()
    os.chdir(git_folder)
    archive_name = "%s-%s.tar" % (project, commit_hash)
    cmd = reader.archive_cmd(project, archive_name)
    LOG.debug('Command to generate archive: %s', ' '.join(cmd))
    pull = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    out = pull.communicate()
    os.chdir(cwd)

    # Update spec file
    spec_file = config.get(project, 'spec_file')
    if '~' in spec_file:
        spec_file = os.path.expanduser(spec_file)

    update_spec(
        spec_file,
        commit_hash,
        archive_name,
        config.get('main', 'username'),
        config.get('main', 'email'),
        reader)

    # Copy patches
    if config.has_option(project, 'patch_files'):
        LOG.info('Copying patches')
        candidates = config.get(project, 'patch_files').split(',')
        candidates = [candidate.strip() for candidate in candidates]
        for candidate in candidates:
            LOG.debug('Expanding path: %s', candidate)
            candidate = os.path.expanduser(candidate)
            patches = glob.glob(candidate)
            if not patches:
                LOG.info('Could not expand path: `%s`', candidate)
            for patch in patches:
                filename = os.path.basename(patch)
                dest = os.path.join(get_rpm_sourcedir(), filename)
                LOG.debug('Copying from %s, to %s', patch, dest)
                shutil.copy(
                    patch,
                    dest
                )

    # Generate SRPM
    env = os.environ
    env['LANG'] = 'C'
    build = subprocess.Popen(
        ["rpmbuild", "-bs", spec_file],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env)
    out = build.communicate()
    os.chdir(cwd)
    if build.returncode:
        LOG.info(
            'Strange result of the rpmbuild -bs:\n  stdout:%s\n  stderr:%s',
            out[0],
            out[1]
        )
        return
    srpm = out[0].split('Wrote:')[1].strip()
    LOG.info('SRPM built: %s', srpm)

    return srpm


def upload_srpms(config, srpms):
    ''' Using the information provided in the configuration file,
    upload the src.rpm generated somewhere.
    '''
    if not config.has_option('main', 'upload_command'):
        raise DgrocException(
            'No `upload_command` specified in the `main` section of the '
            'configuration file.')

    upload_command = config.get('main', 'upload_command')

    for srpm in srpms:
        LOG.debug('Uploading source rpm: %s', srpm)
        cmd = upload_command % srpm
        outcode = subprocess.call(cmd, shell=True)
        if outcode:
            LOG.info('Strange result with the command: `%s`', cmd)


def copr_build(config, srpms):
    ''' Using the information provided in the configuration file,
    run the build in copr.
    '''

    ## dgroc config check
    if not config.has_option('main', 'upload_url'):
        raise DgrocException(
            'No `upload_url` specified in the `main` section of the dgroc '
            'configuration file.')

    if not config.has_option('main', 'copr_url'):
        warnings.warn(
            'No `copr_url` option set in the `main` section of the dgroc '
            'configuration file, using default: %s' % COPR_URL)
        copr_url = COPR_URL
    else:
        copr_url = config.get('main', 'copr_url')

    if not copr_url.endswith('/'):
        copr_url = '%s/' % copr_url

    insecure = False
    if config.has_option('main', 'no_ssl_check') \
            and config.get('main', 'no_ssl_check'):
        warnings.warn(
            "Option `no_ssl_check` was set to True, we won't check the ssl "
            "certificate when submitting the builds to copr")
        insecure = config.get('main', 'no_ssl_check')

    username, login, token = _get_copr_auth()

    build_ids = []
    ## Build project/srpm in copr
    for project in srpms:
        srpms_file = [
            config.get('main', 'upload_url') % (
                srpms[project].rsplit('/', 1)[1])
        ]

        if config.has_option(project, 'copr'):
            copr = config.get(project, 'copr')
        else:
            copr = project

        URL = '%s/api/coprs/%s/%s/new_build/' % (
            copr_url,
            username,
            copr)

        data = {
            'pkgs': ' '.join(srpms_file),
        }

        req = requests.post(
            URL, auth=(login, token), data=data, verify=not insecure)

        if '<title>Sign in Coprs</title>' in req.text:
            LOG.info("Invalid API token")
            return

        if req.status_code == 404:
            LOG.info("Project %s/%s not found.", username, copr)

        try:
            output = req.json()
        except ValueError:
            LOG.info("Unknown response from server.")
            LOG.debug(req.url)
            LOG.debug(req.text)
            return
        if req.status_code != 200:
            LOG.info("Something went wrong:\n  %s", output['error'])
            return
        LOG.info(output)
        if 'id' in output:
            build_ids.append(output['id'])
        elif 'ids' in output:
            build_ids.extend(output['ids'])
    return build_ids


def check_copr_build(config, build_ids):
    ''' Check the status of builds running in copr.
    '''

    ## dgroc config check
    if not config.has_option('main', 'copr_url'):
        warnings.warn(
            'No `copr_url` option set in the `main` section of the dgroc '
            'configuration file, using default: %s' % COPR_URL)
        copr_url = COPR_URL
    else:
        copr_url = config.get('main', 'copr_url')

    if not copr_url.endswith('/'):
        copr_url = '%s/' % copr_url

    insecure = False
    if config.has_option('main', 'no_ssl_check') \
            and config.get('main', 'no_ssl_check'):
        warnings.warn(
            "Option `no_ssl_check` was set to True, we won't check the ssl "
            "certificate when submitting the builds to copr")
        insecure = config.get('main', 'no_ssl_check')

    username, login, token = _get_copr_auth()

    build_ip = []
    ## Build project/srpm in copr
    for build_id in build_ids:

        URL = '%s/api/coprs/build_status/%s/' % (
            copr_url,
            build_id)

        req = requests.get(
            URL, auth=(login, token), verify=not insecure)

        if '<title>Sign in Coprs</title>' in req.text:
            LOG.info("Invalid API token")
            return

        if req.status_code == 404:
            LOG.info("Build %s not found.", build_id)

        try:
            output = req.json()
        except ValueError:
            LOG.info("Unknown response from server.")
            LOG.debug(req.url)
            LOG.debug(req.text)
            return
        if req.status_code != 200:
            LOG.info("Something went wrong:\n  %s", output['error'])
            return
        LOG.info('  Build %s: %s', build_id, output)

        if output['status'] in ('pending', 'running'):
            build_ip.append(build_id)
    return build_ip


def main():
    '''
    '''
    # Retrieve arguments
    args = get_arguments()

    global LOG
    #global LOG
    if args.debug:
        LOG.setLevel(logging.DEBUG)
    else:
        LOG.setLevel(logging.INFO)

    # Read configuration file
    config = ConfigParser.ConfigParser()
    config.read(args.config)

    if not config.has_option('main', 'username'):
        raise DgrocException(
            'No `username` specified in the `main` section of the '
            'configuration file.')

    if not config.has_option('main', 'email'):
        raise DgrocException(
            'No `email` specified in the `main` section of the '
            'configuration file.')

    srpms = {}
    for project in config.sections():
        if project == 'main':
            continue
        LOG.info('Processing project: %s', project)
        try:
            srpm = generate_new_srpm(config, project)
            if srpm:
                srpms[project] = srpm
        except DgrocException, err:
            LOG.info('%s: %s', project, err)

    LOG.info('%s srpms generated', len(srpms))
    if not srpms:
        return

    if args.srpmonly:
        return

    try:
        upload_srpms(config, srpms.values())
    except DgrocException, err:
        LOG.info(err)

    try:
        build_ids = copr_build(config, srpms)
    except DgrocException, err:
        LOG.info(err)

    if args.monitoring:
        LOG.info('Monitoring %s builds...', len(build_ids))
        while build_ids:
            time.sleep(45)
            LOG.info(datetime.datetime.now())
            build_ids = check_copr_build(config, build_ids)


if __name__ == '__main__':
    main()
    #build_ids = [6065]
    #config = ConfigParser.ConfigParser()
    #config.read(DEFAULT_CONFIG)
    #print 'Monitoring builds...'
    #build_ids = check_copr_build(config, build_ids)
    #while build_ids:
        #time.sleep(45)
        #print datetime.datetime.now()
        #build_ids = check_copr_build(config, build_ids)
