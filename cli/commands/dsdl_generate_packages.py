#
# Copyright (c) 2019 UAVCAN Development Team
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel.kirienko@zubax.com>
#

import http
import shutil
import typing
import logging
import pathlib
import zipfile
import requests
import tempfile
import argparse
import pyuavcan
from . import _base


INFO = _base.CommandInfo(
    help='''
Generate PyUAVCAN Python packages from the specified DSDL root namespaces and/or
from URLs pointing to an archive containing a set of DSDL root namespaces.
''',
    examples='''
pyuavcan -vv dsdl-gen-pkg --lookup https://github.com/UAVCAN/public_regulated_data_types/archive/f468909.zip ~/namespace
''',
    aliases=[
        'dsdl-gen-pkg',
    ]
)


_DEFAULT_PUBLIC_REGULATED_DATA_TYPES_ARCHIVE_URL = \
    'https://github.com/UAVCAN/public_regulated_data_types/archive/f468909db282e524eb6410187f02a33720f196d4.zip'


_logger = logging.getLogger(__name__)


def register_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        'input',
        metavar='INPUT_PATH_OR_URI',
        nargs='+',
        help='Either a local path or an URI pointing to the source DSDL root namespace(s). '
             'Can be specified more than once to process multiple namespaces at once. '
             'If the value is a local path, it must point to a local DSDL root namespace directory or '
             'to a local archive containing DSDL root namespace directories at the top level. '
             'If the value is an URI, it must point to an archive containing DSDL root namespace '
             'directories at the top level (this is convenient for generating packages from namespaces '
             'hosted in public repositories, e.g., on GitHub). '
             'Example local path: "~/uavcan/public_regulated_data_types/uavcan/". '
             f'Example URI: "{_DEFAULT_PUBLIC_REGULATED_DATA_TYPES_ARCHIVE_URL}".'
    )
    parser.add_argument(
        '--lookup', '-L',
        action='append',
        metavar='LOOKUP_PATH_OR_URI',
        help='This is like --input, except that the specified DSDL root namespace(s) will be used only for looking up '
             'dependent data types; nothing will be generated from these. '
             'If a DSDL root namespace is specified as an input, it is automatically added to the look-up list. '
             'Can be specified more than once.'
    )
    parser.add_argument(
        '--output', '-O',
        default=_base.DEFAULT_DSDL_GENERATED_PACKAGES_DIRECTORY,
        help='Path to the directory where the generated packages will be stored. '
             'Existing packages will be overwritten entirely. '
             'The destination directory should be in PYTHONPATH to use the generated packages; '
             'the default directory is already added to local package look-up path, '
             'so if the default directory is used, no additional steps are necessary.'
    )
    parser.add_argument(
        '--allow-unregulated-fixed-port-id',
        action='store_true',
        help='Instruct the DSDL front-end to accept unregulated data types with fixed port identifiers. '
             'Make sure you understand the implications before using this option. '
             'If not sure, ask for advice at https://forum.uavcan.org.'
    )


def execute(args: argparse.Namespace) -> None:
    output = pathlib.Path(args.output)
    allow_unregulated_fixed_port_id = bool(args.allow_unregulated_fixed_port_id)

    inputs: typing.List[pathlib.Path] = []
    for location in args.input:
        inputs += _fetch_root_namespace_dirs(location)
    _logger.info('Input DSDL root namespace directories: %r', list(map(str, inputs)))

    lookup: typing.List[pathlib.Path] = []
    for location in (args.lookup or []):
        lookup += _fetch_root_namespace_dirs(location)
    _logger.info('Lookup DSDL root namespace directories: %r', list(map(str, lookup)))

    gpi_list = _generate_dsdl_packages(source_root_namespace_dirs=inputs,
                                       lookup_root_namespace_dirs=lookup,
                                       generated_packages_dir=output,
                                       allow_unregulated_fixed_port_id=allow_unregulated_fixed_port_id)
    for gpi in gpi_list:
        _logger.info('Generated package %r with %d data types at %r', gpi.name, len(gpi.models), str(gpi.path))


def _fetch_root_namespace_dirs(location: str) -> typing.List[pathlib.Path]:
    print('location', location)
    if '://' in location:
        dirs = _fetch_archive_dirs(location)
        _logger.info('Resource %r contains the following root namespace directories: %r',
                     location, list(map(str, dirs)))
        return dirs
    else:
        return [pathlib.Path(location)]


def _fetch_archive_dirs(archive_url: str) -> typing.List[pathlib.Path]:
    """
    Downloads an archive from the specified URL, unpacks it into a temporary directory, and returns the list of
    directories in the root of the unpacked archive.
    """
    # TODO: autodetect the type of the archive
    arch_dir = tempfile.mkdtemp(prefix='pyuavcan-cli-dsdl')
    arch_file = str(pathlib.Path(arch_dir) / 'dsdl.zip')

    _logger.debug('Downloading the archive from %r into %r...', archive_url, arch_file)
    response = requests.get(archive_url)
    if response.status_code != http.HTTPStatus.OK:
        raise RuntimeError(f'Could not download the archive; HTTP error {response.status_code}')
    with open(arch_file, 'wb') as f:
        f.write(response.content)

    _logger.debug('Extracting the archive into %r...', arch_dir)
    with zipfile.ZipFile(arch_file) as zf:
        zf.extractall(arch_dir)

    inner, = [d for d in pathlib.Path(arch_dir).iterdir() if d.is_dir()]  # Strip the outer layer, we don't need it

    assert isinstance(inner, pathlib.Path)
    return [d for d in inner.iterdir() if d.is_dir()]


def _generate_dsdl_packages(source_root_namespace_dirs:      typing.Iterable[pathlib.Path],
                            lookup_root_namespace_dirs:      typing.Iterable[pathlib.Path],
                            generated_packages_dir:          pathlib.Path,
                            allow_unregulated_fixed_port_id: bool) \
        -> typing.Sequence[pyuavcan.dsdl.GeneratedPackageInfo]:
    lookup_root_namespace_dirs = frozenset(list(lookup_root_namespace_dirs) + list(source_root_namespace_dirs))
    generated_packages_dir.mkdir(parents=True, exist_ok=True)

    out: typing.List[pyuavcan.dsdl.GeneratedPackageInfo] = []
    for ns in source_root_namespace_dirs:
        dest_dir = generated_packages_dir / ns.name
        _logger.info('Generating DSDL package %r from root namespace %r with lookup dirs: %r',
                     dest_dir, ns, list(map(str, lookup_root_namespace_dirs)))
        shutil.rmtree(dest_dir, ignore_errors=True)
        gpi = pyuavcan.dsdl.generate_package(package_parent_directory=generated_packages_dir,
                                             root_namespace_directory=ns,
                                             lookup_directories=lookup_root_namespace_dirs,
                                             allow_unregulated_fixed_port_id=allow_unregulated_fixed_port_id)
        out.append(gpi)
    return out