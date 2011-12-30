#!/usr/bin/env python
import compiler
import os
import sys

from subprocess import Popen, PIPE


class ASTVisitor(compiler.visitor.ASTVisitor):
    """Visitor that turns module attributes into a dict.

    Instances of this class are to be fed into the second parameter of
    compiler.visitor.walk.

    """
    items = {}

    def __getitem__(self, key):
        return self.items[key]

    def visitAssign(self, node):
        """Called for each Assign node in the tree."""
        name_node = node.getChildren()[0]
        value_node = node.getChildren()[1]

        name = name_node.name
        value = None

        # Scalars.
        if hasattr(value_node, 'value'):
            value = value_node.value

        # Lists.
        elif hasattr(value_node, 'nodes'):
            value = [x.value for x in value_node.nodes]

        self.items[name] = value


class ShellError(Exception):
    def __init__(self, command, returncode, stdout=None, stderr=None):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        buf = "'%s' returned non-zero exit status %s" % (
            self.command, self.returncode)

        if self.stdout:
            buf = "%s\n\n--- STDOUT ---\n%s" % (buf, self.stdout)

        if self.stderr:
            buf = "%s\n\n--- STDERR ---\n%s" % (buf, self.stderr)

        return buf

    def __repr__(self):
        return str(self)


def shell(command):
    """Helper method to get the output from a command."""
    p = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)
    stdout, stderr = p.communicate()

    if p.returncode != 0:
        raise ShellError(command, p.returncode, stdout, stderr)

    return stdout


class ZenossManager(object):
    def setup(self, zenoss_version, zenoss_flavor):
        self.tear_down()

        try:
            if zenoss_version.startswith('3'):
                shell("sudo /sbin/service mysqld start")
            elif zenoss_version.startswith('4'):
                shell("sudo /sbin/service zends start")
                shell("sudo /sbin/service rabbitmq-server start")
        except ShellError:
            pass

        lv_name = "zenoss/%s_%s" % (zenoss_version, zenoss_flavor)
        lv_device = "/dev/%s" % lv_name

        if not os.path.exists(lv_device):
            raise Exception("%s doesn't exist." % lv_device)

        try:
            shell("sudo /usr/sbin/lvcreate -l25%%ORIGIN -s -n sandbox %s" % lv_name)
        except ShellError:
            pass

        try:
            shell("sudo mount /dev/zenoss/sandbox /opt/zenoss")
            shell("sudo /sbin/service zenoss start")
        except ShellError, ex:
            print ex
            sys.exit(1)

    def tear_down(self):
        commands = (
            "sudo /sbin/service zenoss stop",
            "sudo umount /opt/zenoss",
            "sudo /usr/sbin/lvremove -f zenoss/sandbox",
            )

        for command in commands:
            try:
                shell(command)
            except ShellError:
                pass


class ZenPackBuilder(object):
    zenpack_name = None
    zenpack_directory = None

    def __init__(self):
        self.zenpack_directory = os.getcwd()

        tree = compiler.parseFile(os.path.join(
            self.zenpack_directory, 'setup.py'))

        visitor = compiler.visitor.walk(tree, ASTVisitor())

        self.zenpack_name = visitor['NAME']

    def run_all(self):
        self.build_egg()

        self.test_install()
        self.test_unittests()

    def build_egg(self):
        try:
            shell("sudo chmod 775 .")
            shell("sudo chown -R zenoss:jenkins .")
            shell("sudo rm -Rf build dist *.egg-info")

            print "*** Building ZenPack Egg"
            print shell(
                "sudo -u zenoss -i "
                "'cd %s ; python setup.py bdist_egg'" % (
                    self.zenpack_directory))

            shell("sudo -u zenoss -i mkdir -p /opt/zenoss/zenpack_eggs")
            shell("sudo cp dist/*.egg /opt/zenoss/zenpack_eggs/")
        except ShellError, ex:
            print ex
            sys.exit(1)

    def test_install(self):
        try:
            print "*** Installing ZenPack Egg"
            print shell(
                "sudo -u zenoss -i zenpack --install "
                "/opt/zenoss/zenpack_eggs/%s-*.egg 2>&1" % (
                    self.zenpack_name))

        except ShellError, ex:
            print ex
            sys.exit(1)

    def test_unittests(self):
        try:
            print "*** Running ZenPack Unit Tests"
            print shell(
                "sudo -u zenoss -i nosetests "
                "-w /opt/zenoss/ZenPacks/%(name)s-*.egg/ZenPacks "
                "--with-coverage --cover-package=%(name)s "
                "%(name)s.tests 2>&1" % (
                    {'name': self.zenpack_name}),)

        except ShellError, ex:
            print ex
            sys.exit(1)


def main():
    build_tag = os.environ.get('BUILD_TAG', None)
    if not build_tag:
        print >> sys.stderr, "BUILD_TAG environment variable not set."
        sys.exit(1)

    build_labels = dict(
        x.split('=') for x in build_tag.split('-')[1].split(','))

    zenoss_version = build_labels.get('zenoss_version', None)
    if not zenoss_version:
        print >> sys.stderr, "BUILD_TAG doesn't contain zenoss_version."
        sys.exit(1)

    zenoss_flavor = build_labels.get('zenoss_flavor', None)
    if not zenoss_flavor:
        print >> sys.stderr, "BUILD_TAG doesn't contain zenoss_flavor."
        sys.exit(1)

    if not os.path.isfile('setup.py'):
        print >> sys.stderr, "setup.py doesn't exist."
        sys.exit(1)

    zman = ZenossManager()
    print "*** Setting up environment for Zenoss %s (%s)" % (
        zenoss_version, zenoss_flavor)

    zman.setup(zenoss_version, zenoss_flavor)

    try:
        tester = ZenPackBuilder()
        tester.run_all()
    finally:
        print "*** Tearing down environment for Zenoss %s (%s)" % (
            zenoss_version, zenoss_flavor)

        zman.tear_down()


if __name__ == '__main__':
    main()
