import glob
import shutil

from textwrap import dedent

from mockbuild import util
from mockbuild.exception import BuildError

def PackageManager(config_opts, chroot):
    pm = config_opts.get('package_manager', 'yum')
    if pm == 'yum':
        return Yum(config_opts, chroot)
    elif pm == 'dnf':
        return Dnf(config_opts, chroot)
    else:
        #TODO specific exception type
        raise Exception('Unrecognized package manager')


class _PackageManager(object):
    command = None

    def __init__(self, config, buildroot):
        self.config = config
        self.buildroot = buildroot

    def build_invocation(self, *args):
        if args[0] == 'builddep':
            args = args[1:]
            invocation = self.builddep_command
            common_opts = self.config[self.command + '_builddep_opts']
        else:
            invocation = [self.command]
            common_opts = self.config[self.command + '_common_opts']
        invocation += ['--installroot', self.buildroot.makeChrootPath()]
        releasever = self.config['releasever']
        if releasever:
            invocation += ['--releasever', releasever]
        if not self.config['online']:
            invocation.append('-C')
        invocation += common_opts
        invocation += args
        return invocation

    def execute(self, *args, **kwargs):
        self.buildroot._callHooks("preyum")
        env = self.config['environment'].copy()
        env.update(util.get_proxy_environment(self.config))
        env['LC_MESSAGES'] = 'C'
        invocation = self.build_invocation(*args)
        self.buildroot.root_log.debug(invocation)
        # log?
        self.buildroot._nuke_rpm_db()
        out = util.do(invocation, env=env, **kwargs)
        self.buildroot._callHooks("postyum")
        return out

    def install(self, *args):
        return self.execute('install', *args)

    def remove(self, *args):
        return self.execute('remove', *args)

    def update(self, *args):
        return self.execute('update', *args)

    def builddep(self, *args, **kwargs):
        return self.execute('builddep', *args)

    def initialize_config(self):
        raise NotImplementedError()

class Yum(_PackageManager):
    command = 'yum'
    builddep_command = ['yum-builddep']

    def _write_plugin_conf(self, name):
        """ Write 'name' file into pluginconf.d """
        conf_path = self.buildroot.makeChrootPath('etc', 'yum', 'pluginconf.d', name)
        with open(conf_path, 'w+') as conf_file:
            conf_file.write(self.config[name])

    def initialize_config(self):
        # use yum plugin conf from chroot as needed
        pluginconf_dir = self.buildroot.makeChrootPath('etc', 'yum', 'pluginconf.d')
        util.mkdirIfAbsent(pluginconf_dir)
        config_content = self.config['yum.conf']\
                          .replace("plugins=1",
                           dedent("""\
                           plugins=1
                           pluginconfpath={0}""".format(pluginconf_dir)))

        # write in yum.conf into chroot
        # always truncate and overwrite (w+)
        self.buildroot.root_log.debug('configure yum')
        yumconf_path = self.buildroot.makeChrootPath('etc', 'yum', 'yum.conf')
        with open(yumconf_path, 'w+') as yumconf_file:
            yumconf_file.write(config_content)

        # write in yum plugins into chroot
        self.buildroot.root_log.debug('configure yum priorities')
        self._write_plugin_conf('priorities.conf')
        self.buildroot.root_log.debug('configure yum rhnplugin')
        self._write_plugin_conf('rhnplugin.conf')
        if self.config['subscription-manager.conf']:
            self.buildroot.root_log.debug('configure RHSM rhnplugin')
            self._write_plugin_conf('subscription-manager.conf')
            pem_dir = self.buildroot.makeChrootPath('etc', 'pki', 'entitlement')
            util.mkdirIfAbsent(pem_dir)
            for pem_file in glob.glob("/etc/pki/entitlement/*.pem"):
                shutil.copy(pem_file, pem_dir)
            consumer_dir = self.buildroot.makeChrootPath('etc', 'pki', 'consumer')
            util.mkdirIfAbsent(consumer_dir)
            for consumer_file in glob.glob("/etc/pki/consumer/*.pem"):
                shutil.copy(consumer_file, consumer_dir)
            shutil.copy('/etc/rhsm/rhsm.conf',
                    self.buildroot.makeChrootPath('etc', 'rhsm'))
            self.execute('repolist')

        # Copy RPM GPG keys
        pki_dir = self.buildroot.makeChrootPath('etc', 'pki', 'mock')
        util.mkdirIfAbsent(pki_dir)
        for pki_file in glob.glob("/etc/pki/mock/RPM-GPG-KEY-*"):
            shutil.copy(pki_file, pki_dir)

    def install(self, *pkgs, **kwargs):
        out = self.execute('resolvedep', *pkgs, returnOutput=True,
                           printOutput=False, pty=False)
        _check_missing(out)
        out = super(Yum, self).install(*pkgs, **kwargs)
        _check_missing(out)

    def builddep(self, *pkgs, **kwargs):
        out = super(Yum, self).builddep(*pkgs, **kwargs)
        _check_missing(out)

def _check_missing(output):
    for i, line in enumerate(output.split('\n')):
        for msg in ('no package found for', 'no packages found for',
                    'missing dependency', 'error:'):
            if msg in line.lower():
                raise BuildError('\n'.join(output.split('\n')[i:]))

class Dnf(_PackageManager):
    command = 'dnf'
    builddep_command = ['dnf', 'builddep']

    def build_invocation(self, *args):
        if not 'dnf_builddep_opts' in self.config:
            self.config['dnf_builddep_opts'] = self.config['yum_builddep_opts']
        if not 'dnf_common_opts' in self.config:
            self.config['dnf_common_opts'] = self.config['yum_common_opts']
        return super(Dnf, self).build_invocation(*args)

    def initialize_config(self):
        if 'dnf.conf' in self.config:
            config_content = self.config['dnf.conf']
        else:
            config_content = self.config['yum.conf']
        util.mkdirIfAbsent(self.buildroot.makeChrootPath('etc', 'dnf'))
        dnfconf_path = self.buildroot.makeChrootPath('etc', 'dnf', 'dnf.conf')
        with open(dnfconf_path, 'w+') as dnfconf_file:
            dnfconf_file.write(config_content)