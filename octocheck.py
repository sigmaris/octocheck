# coding: utf-8
"""
This script parses the output of some linters, build and testing tools,
and reports them as Checks to Github (https://github.blog/2018-05-07-introducing-checks-api/).
This lets you annotate pull requests with richer results than pass/fail status.
This script is useful for any type of CI service which doesn't have a built in
Github App integration, for example Jenkins, Buildbot or any custom CI system.
"""
import abc
import argparse
import datetime
import enum
import glob
import itertools
import json
import os
import subprocess

try:
    from lxml import etree
except ImportError:
    from xml.etree import ElementTree as etree

import github3


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


class Status(enum.IntEnum):
    SUCCESS = 1
    NEUTRAL = 2
    FAILURE = 3


class Parser(metaclass=abc.ABCMeta):

    def __init__(self):
        self.annotations = set()
        self.status = Status.SUCCESS

    def get_annotations(self):
        return self.annotations

    def get_status(self):
        return self.status

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

    @staticmethod
    @abc.abstractmethod
    def arg_name():
        pass

    @staticmethod
    @abc.abstractmethod
    def display_name():
        pass


class Pep8Parser(Parser):

    @staticmethod
    def arg_name():
        return 'pep8'

    @staticmethod
    def display_name():
        return 'PEP8'

    def parse_fileobj(self, fileobj):
        for line in fileobj:
            try:
                path, line, column, *msgparts = line.split(':')
            except ValueError:
                continue
            raw_message = ':'.join(msgparts)
            message = raw_message.strip()
            if message.startswith('E'):
                level = 'failure'
            else:
                level = 'warning'
            self.annotations.add(Annotation(
                path, int(line), int(line), level, message,
                start_column=int(column), end_column=int(column)
            ))
            self.status = Status.FAILURE


class CargoJSONParser(Parser):

    @staticmethod
    def arg_name():
        return 'cargo'

    @staticmethod
    def display_name():
        return 'Cargo JSON'

    def parse_fileobj(self, fileobj):
        for line in fileobj:
            try:
                msg_obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg_obj.get('reason') == 'compiler-message' and msg_obj.get('message'):
                message = msg_obj['message']
                self._annotation_from_message(message)

    def _get_primary_span_from_message(self, message):
        spans = message.get('spans', [])
        primaries = [span for span in spans if span.get('is_primary', False)]
        if primaries:
            return primaries[0]
        else:
            return None

    def _annotation_from_message(self, message, parent=None):
        title = message.get('message')

        level = message.get('level')
        if level is None:
            return

        if 'error' in level:
            ann_level = 'failure'
            self.status = Status.FAILURE
        elif 'warning' in level:
            ann_level = 'warning'
            self.status = Status.FAILURE
        else:
            ann_level = 'notice'

        rendered = message.get('rendered')
        if rendered:
            base_raw_details = rendered + '\n'
        else:
            base_raw_details = ''

        code = message.get('code')
        if code is not None:
            err_code = code.get('code')
            expl = code.get('explanation')
            if err_code:
                base_raw_details += f"Error code {err_code}\n"
            if expl:
                base_raw_details += expl

        spans = message.get('spans', [])

        if parent:
            primary_span = self._get_primary_span_from_message(parent)
        else:
            primary_span = self._get_primary_span_from_message(message)

        primary_ref = ''
        if primary_span:
            primary_path = primary_span.get('file_name')
            primary_line = primary_span.get('line_start')
            if primary_path and primary_line:
                primary_ref = f"{primary_path}#{primary_line}"
        if primary_ref:
            title = f"{primary_ref}: {title}"

        for span in spans:
            span_raw_details = base_raw_details
            path = span.get('file_name')
            if path is None:
                continue

            line_start = span.get('line_start')
            if line_start is None:
                continue

            line_end = span.get('line_end')
            if line_end is None:
                line_end = line_start

            suggested_replacement = span.get('suggested_replacement')

            label = span.get('label')
            if label is None:
                if suggested_replacement:
                    label = f"Suggested replacement: {suggested_replacement}"
                elif title is not None:
                    label = title
                else:
                    continue

            optionals = {}
            if line_start == line_end:
                optionals['start_column'] = span.get('column_start')
                optionals['end_column'] = span.get('column_end')
            if span_raw_details:
                optionals['raw_details'] = span_raw_details
            if title:
                optionals['title'] = title

            self.annotations.add(Annotation(
                path, int(line_start), int(line_end), ann_level, label, **optionals
            ))

        children = message.get('children', [])
        for child in children:
            self._annotation_from_message(child, message)


class XUnitParser(Parser):

    @staticmethod
    def arg_name():
        return 'xunit'

    @staticmethod
    def display_name():
        return 'xUnit'

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
                    self.status = Status.FAILURE
                for failure in case.iterfind('failure'):
                    self._annotation_from_case(case, failure)
                    self.status = Status.FAILURE

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

        ann_kwargs = {}
        if error.text:
            ann_kwargs['raw_details'] = error.text[:65536]

        self.annotations.add(
            Annotation(path, int(line), int(line), 'failure', message, **ann_kwargs)
        )


_parsers = [CargoJSONParser, Pep8Parser, XUnitParser]


def _get_current_commit():
    try:
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip().lower()
        int(sha, 16)
        if len(sha) == 40:
            return sha
        else:
            return None
    except Exception:
        return None


_config = {
    'app_id': {
        'required': True,
        'type': str,
        'help': "Github App ID - get this from the Github App Developer Settings.",
    },
    'priv_key_file': {
        'required': True,
        'type': str,
        'help': "Github App private key file - download this from the Github App Developer Settings.",
    },
    'gh_owner': {
        'required': True,
        'type': str,
        'help': "Github repository owner (personal account or organisation).",
    },
    'gh_repo': {
        'required': True,
        'type': str,
        'help': "Github repository name.",
    },
    'commit': {
        'required': True,
        'type': str,
        'default': _get_current_commit(),
        'help': "Git commit hash to submit the check for."
                " Defaults to HEAD of the git repository in the current directory.",
    },
    'add_prefix': {
        'required': False,
        'type': str,
        'help': "Add this prefix to all file paths being annotated."
                " The resulting paths in the the annotations should be relative to the root of the Github repository.",
    },
    'del_prefix': {
        'required': False,
        'type': str,
        'help': "Remove this prefix, if present, from all the file paths being annotated."
                " Can be useful if your report files use absolute paths, or have paths prefixed with, e.g. './'."
                " The resulting paths in the the annotations should be relative to the root of the Github repository.",
    },
    'check_name': {
        'required': False,
        'type': str,
        'default': 'octocheck',
        'help': "Name of this check, will be displayed in the Github UI.",
    },
    'details_url': {
        'required': False,
        'type': str,
        'help': "A URL which will be displayed in the Github UI to see more details of this check."
                " For example, if this is run as part of a CI build, you could put the URL to the CI build page.",
    },
    'title': {
        'required': False,
        'type': str,
        'default': 'OctoCheck reporter',
        'help': "The title of this check, will be displayed in the Github UI.",
    },
}
_config.update({
    p.arg_name(): {
        'required': False,
        'type': list,
        'help': f"Glob pattern to find {p.display_name()} input files."
    }
    for p in _parsers
})


def _get_argparser(config=_config):
    argparser = argparse.ArgumentParser(description=__doc__)
    for key, opts in config.items():
        ap_args = []
        ap_kwargs = {}

        ap_args.append('--{}'.format(key.replace('_', '-')))
        ap_kwargs['dest'] = key
        ap_kwargs['help'] = opts['help']

        env_val = os.environ.get(f"OC_{key.upper()}")
        if env_val:
            ap_kwargs['default'] = env_val
        elif opts.get('default'):
            ap_kwargs['default'] = opts['default']

        if opts['required'] and not ap_kwargs.get('default'):
            ap_kwargs['required'] = True

        if opts['type'] == list:
            ap_kwargs['nargs'] = '+'

        argparser.add_argument(*ap_args, **ap_kwargs)
    return argparser


def cli():
    ap = _get_argparser()
    args = ap.parse_args()

    gh_app = github3.GitHub()
    with open(args.priv_key_file, 'rb') as privkey:
        privkey_bytes = privkey.read()

    try:
        gh_app.login_as_app(privkey_bytes, args.app_id)
    except Exception:
        return "Couldn't authenticate as a GitHub app"

    try:
        installation = gh_app.app_installation_for_repository(args.gh_owner, args.gh_repo)
    except Exception:
        return "Couldn't find an installation of this GitHub app on that repository"

    try:
        gh_inst = github3.GitHub()
        gh_inst.login_as_app_installation(privkey_bytes, args.app_id, installation.id)
    except Exception:
        return "Couldn't authenticate as app installation"

    try:
        repo = gh_inst.repository(args.gh_owner, args.gh_repo)
    except Exception:
        return "Couldn't find repository"

    parser_outputs = {
        p: {'annotations': set(), 'file_info': set()}
        for p in _parsers
    }
    overall_status = Status.SUCCESS

    for parser in _parsers:
        patterns = getattr(args, parser.arg_name())
        if patterns:
            for pattern in patterns:
                for file in glob.iglob(pattern, recursive=True):
                    parser_obj = parser()
                    parser_obj.parse_file(file)
                    anns = parser_obj.get_annotations()
                    status = parser_obj.get_status()
                    outputs = parser_outputs[parser]
                    outputs['annotations'].update(anns)
                    display_name = parser.display_name()
                    outputs['file_info'].add(
                        f"{display_name} file {file}: {len(anns)} annotations, status {status.name}"
                    )
                    overall_status = max(overall_status, status)

    all_annotations = set(itertools.chain.from_iterable(o['annotations'] for o in parser_outputs.values()))
    all_files = set(itertools.chain.from_iterable(o['file_info'] for o in parser_outputs.values()))
    details = ''
    for p in _parsers:
        output = parser_outputs[parser]
        if output['file_info']:
            file_count = len(output['file_info'])
            ann_count = len(output['annotations'])
            display_name = p.display_name()
            details += f"{file_count} {display_name} files parsed, {ann_count} {display_name} annotations.\n\n"
    output = {
        'title': args.title,
        'summary': f"{len(all_files)} files parsed, {len(all_annotations)} annotations in total."
    }
    if details:
        output['text'] = details

    # Group into batches of 50 annotations
    groups = itertools.zip_longest(*[iter(all_annotations)] * 50)
    check_run = None
    for x in groups:
        this_batch_annotations = []
        for ann in itertools.takewhile(lambda i: i is not None, x):
            path = ann.path
            if args.del_prefix and path.startswith(args.del_prefix):
                path = path[len(args.del_prefix):]
            if args.add_prefix:
                path = args.add_prefix + path
            ann_dict = {
                'path':  path,
                'start_line':  ann.start_line,
                'end_line':  ann.end_line,
                'annotation_level':  ann.level,
                'message':  ann.message,
            }
            for optional in ('start_column', 'end_column', 'title', 'raw_details'):
                if getattr(ann, optional):
                    ann_dict[optional] = getattr(ann, optional)
            this_batch_annotations.append(ann_dict)
        this_batch_output = dict(**output)
        this_batch_output['annotations'] = this_batch_annotations
        if check_run is None:
            utcnow = datetime.datetime.utcnow()
            check_run = repo.create_check_run(
                name=args.check_name, head_sha=args.commit, details_url=args.details_url,
                conclusion=overall_status.name.lower(), completed_at=utcnow.strftime("%Y-%m-%dT%H:%M:%SZ"),
                output=this_batch_output
            )
        else:
            check_run.update(output=this_batch_output)


if __name__ == '__main__':
    cli()
