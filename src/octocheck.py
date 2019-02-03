import abc
import argparse
import glob
import os

try:
    from lxml import etree
except ImportError:
    from xml.etree import ElementTree as etree

import github3


_config = {
    'app_id': dict(required=True, type=str),
    'priv_key_file': dict(required=True, type=str),
    'pep8': dict(required=False, type=list),
    'xunit': dict(required=False, type=list),
}

def _get_argparser(config=_config):
    argparser = argparse.ArgumentParser()
    for key, opts in config.items():
        ap_args = []
        ap_kwargs = {}

        ap_args.append('--{}'.format(key.replace('_', '-')))
        ap_kwargs['dest'] = key

        env_val = os.environ.get(f"OC_{key.upper()}")
        if env_val:
            ap_kwargs['default'] = env_val

        if opts['required']:
            ap_kwargs['required'] = True

        if opts['type'] == list:
            ap_kwargs['nargs'] = '+'

        argparser.add_argument(*ap_args, **ap_kwargs)
    return argparser


class Annotation(object):

    __slots__ = ('path', 'start_line', 'end_line', 'level', 'message',
                 'start_column', 'end_column', 'title', 'raw_details')
    def __init__(
            self, path, start_line, end_line, level, message,
            start_column=None, end_column=None, title=None, raw_details=None
    ):
        for slot in self.__slots__:
            setattr(self, slot, locals()[slot])

    def __hash__(self):
        return hash(tuple(getattr(self, attr) for attr in self.__slots__))

    def __eq__(self, other):
        if other is self:
            return True
        if isinstance(other, Annotation):
            return all(
                getattr(self, attr) == getattr(other, attr)
                for attr in self.__slots__
            )

    def __repr__(self):
        return "Annotation({})".format(", ".join(
            "{}={}".format(attr, repr(getattr(self, attr)))
            for attr in self.__slots__
        ))



class Parser(metaclass=abc.ABCMeta):

    def __init__(self):
        self.annotations = set()

    def get_annotations(self):
        return self.annotations

    def parse_file(self, file, encoding='utf-8'):
        try:
            if isinstance(file, str):
                fileobj = open(file, 'r', encoding=encoding)
            else:
                fileobj = file
            self.parse_fileobj(fileobj)
        finally:
            if isinstance(file, str):
                fileobj.close()

    @abc.abstractmethod
    def parse_fileobj(self, fileobj):
        pass


class Pep8Parser(Parser):

    def parse_fileobj(self, fileobj):
        for line in fileobj:
            try:
                path, line, column, *msgparts = line.split(':')
            except ValueError:
                continue
            raw_message = ':'.join(msgparts)
            message = raw_message.strip()
            # TODO: level mapping?
            self.annotations.add(Annotation(path, int(line), int(line), 'warning', message, start_column=int(column), end_column=int(column)))


class XUnitParser(Parser):

    def parse_fileobj(self, fileobj):
        root_elem = etree.fromstring(fileobj.read())
        if root_elem.tag == "testsuites":
            suites = root_elem.iterfind('testsuite')
        elif root_elem.tag == "testsuite":
            suites = [root_elem]
        else:
            raise ValueError("Invalid XUnit format.")

        for suite in suites:
            for case in suite.iterfind('testcase'):
                for error in case.iterfind('error'):
                    self._annotation_from_case(case, error)
                for failure in case.iterfind('failure'):
                    self._annotation_from_case(case, failure)

    def _annotation_from_case(self, case, error):
        if error.attrib.get('message'):
            message = error.attrib['message']
        elif error.attrib.get('type'):
            message = error.attrib['type']
        else:
            return

        if case.attrib.get('file'):
            path = case.attrib['file']
        else:
            return

        if case.attrib.get('line'):
            line = case.attrib['line']
        else:
            return

        self.annotations.add(
            Annotation(path, int(line), int(line), 'failure', message)
        )


def cli():
    ap = _get_argparser()
    args = ap.parse_args()

    gh = github3.GitHub()
    with open(args.priv_key_file, 'rb') as privkey:
        gh.login_as_app(privkey.read(), args.app_id)
    if args.pep8:
        pep8 = Pep8Parser()
        for pattern in args.pep8:
            for file in glob.iglob(pattern, recursive=True):
                pep8.parse_file(file)
        print(pep8.get_annotations())
    if args.xunit:
        xunit = XUnitParser()
        for pattern in args.xunit:
            for file in glob.iglob(pattern, recursive=True):
                xunit.parse_file(file)
        print(xunit.get_annotations())


if __name__ == '__main__':
    cli()
