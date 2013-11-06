# -*- coding: utf-8 -*-
import logging
import os
import subprocess
import json
from itertools import chain
import tablib
from django.conf import settings
from django.db.models.loading import get_model
from django.db.utils import DatabaseError
from openbudgets.apps.entities.models import Domain, Division, Entity
from django.db.models.fields import FieldDoesNotExist

#Sets the loggin to print to the console
logging.getLogger().setLevel(logging.INFO)

class Store(object):


    """Takes a model and an object, and saves to the data store.

    Can be subclassed to provide custom save methods following the
    convention of _save_{model_name_lower_case}

    """

    def __init__(self, model, obj):


        self.model = model
        self.obj = obj
        self.direct_relation_types = [
            "ForeignKey",
            "ManyToManyField",
            "OneToOneField"
        ]


    def save(self):
        try:
            save_method = getattr(self, '_save_' + self.model.__name__.lower())
        except AttributeError as e:
            save_method = self._save_base
        return save_method(**self.obj)

    def _save_base(self, **obj):
        obj = self.prepare_obj(**obj)
        try:
            obj = self.model.objects.get(**obj)
        except self.model.DoesNotExist:

            obj = self.model.objects.create(**obj)
        return obj

    def prepare_obj(self, **obj):
        for key in obj.keys():

            lookup_fields = settings.IMPORT_PRIORITY
            related_model = None
            header = key

            if settings.IMPORT_SEPARATOR in key:
                header, header_args = key.split(settings.IMPORT_SEPARATOR)
                # TODO: improve the way we take args add add them for lookups. should be namespaced
                # TODO: also, try the accessor syntax from django `model__field_name`
                lookup_fields.insert(0, header_args)

            try:
                # If the field is actually defined on the class, we'll get the related_model like this
                model_field = self.model._meta.get_field(header)
                if model_field.get_internal_type() in self.direct_relation_types:
                    related_model = model_field.rel.to

            except FieldDoesNotExist as e:
                # The field was not on the class, it is a reverse relation in the ORM
                try:
                    model_field = getattr(self.model, header)
                    related_model = model_field.related.model

                except AttributeError as e:
                    #TODO: we actually are raising here when the try/except it is wrapped in
                    # also fails, and thus the user cant know which error to debug
                    raise e

            if related_model:

                related_success = False

                for field in lookup_fields:
                    try:
                        obj[header] = related_model.objects.get(**{field: obj[header]})
                        related_success = True
                        break

                    except related_model.DoesNotExist as e:
                        pass

                if not related_success:
                    # raising here so our loop above executes as desired
                    raise e

        return obj


        # def _save_{model_name_lower_case}(self, **obj):
        #
        #     HERE is the place for any custom code to clean the item
        #     before passing it to _save_base_.
        #     After doing the custom work, ensure you have an header that can be passed to _save_base
        #
        #     return self._save_base(**obj)


class Process(object):

    """Takes data, as list of tuples, validates, and saves to the data store.

    Each tuple passed in the list has the following signature:

    (module, model, source)

    Where:

    * *module* describes a python module in the project that holds *model*
    * *source* is the file with data for *model*

    From this tuple, we map the data to a model and it's save method.

    """

    def __init__(self, freight, storage_class=Store):

        if not isinstance(freight, (list, tuple)):
            raise AssertionError("Store requires a list or a tuple, you passed neither.")

        self.freight = freight
        self.storage_class = storage_class
        self.save()

    def processed(self):
        """Extract data from the source files, clean it, and return a list of (model, obj_dict) tuples."""

        processed = []

        for box in self.freight:
            model, path = box
            raw_dataset = self._extract_data(path)
            data = self._clean_data(raw_dataset)
            processed.append((model, data, path))
        return processed

    def save(self):
        """Unpack our processed data and pass each object to storage class for saving."""

        for item in self.processed():
            model, data, path = item
            obj_list=[]
            for obj in data:
                store = self.storage_class(model, obj)
                obj = store.save()
                obj_list.append(obj)
            self.update_id(obj_list, path)
            self.commit_and_push(path)

    def commit_and_push(self, path):
        """ Add, commit and push the update data to the git repo"""

        to_dir_params = [
            "cd",
            os.path.abspath(os.path.join(settings.OPENBUDGETS_DATA['directory'], 'dataset'))
        ]
        add_params = [
            'git',
            'add',
            path
        ]
        commit_params = [
            'git',
            'commit',
            '-m',
            settings.IMPORT_COMMIT_MESSAGE + os.path.basename(path)
        ]
        push_params = [
            'git',
            'push'
        ]

        params = to_dir_params + ['&']  + add_params + ['&'] + commit_params +['&'] + push_params
        casper= subprocess.Popen(params, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, shell=True)
        casper_output, casper_errors = casper.communicate()

        logging.info(casper_output)
        logging.info(casper_errors)
        logging.info(casper.returncode)

    def update_id(self, obj_list, path):
        """ Update the id of the given objects in the given file path
        """
        f = open(path, 'r+')
        stream = f.read()
        raw_dataset = tablib.import_set(stream)
        del raw_dataset["ID"]
        obj_index= []
        for obj in obj_list:
            for index, name in enumerate(raw_dataset['NAME']):
                if name in obj.name:
                    obj_index.insert(index, obj.id)
        raw_dataset.insert_col(0,set(obj_index), header = "ID")
        f.close()
        f= open(path, 'w')
        f.write(raw_dataset.csv)
        f.close()

    def _extract_data(self, source):
        """Create a Dataset object from the data source."""

        with open(source) as f:
            stream = f.read()
            raw_dataset = tablib.import_set(stream)

        return raw_dataset

    def _clean_data(self, raw_dataset):
        """Takes the raw Dataset and cleans it up."""

        dataset = self._normalize_headers(raw_dataset)
        data = self._normalize_objects(dataset)

        return data

    def _normalize_headers(self, dataset):
        """Clean up the headers of each Dataset."""

        symbols = {
            # Note: We are now allowing the "_" symbol which is valid in python vars.
            ord('-'): None,
            ord('"'): None,
            ord(' '): None,
            ord("'"): None,
            }

        for index, header in enumerate(dataset.headers):
            tmp = unicode(header).translate(symbols).lower()
            dataset.headers[index] = tmp

        return dataset

    def _normalize_objects(self, dataset):
        """Clean up each object in the Dataset."""

        data = dataset.dict

        for item in data:
            for k, v in item.iteritems():
                if not v:
                    del item[k]

        return data


class Unload(object):

    """Extracts a full dataset from a path, and sorts the data for further processing.

    Unload walks the dataset directories, top down, from a root directory.

    Using the index files, which declare the *order* that the data should
    eventually be loaded, and file + directory naming conventions, which
    declare the modules and models that the data is intended for, we
    return a list of tuples, where each tuple has the target Model, and the
    path to the data for that model.

    This list is returned by the `freight` method, and is intended to be consumed
    by the Process class, which further processed the data and prepares it for
    saving to the data store.

    """

    def __init__(self, data_root, ignore_dirs=('assets',), index_file='index.json', supported_extensions=('.csv',)):

        self.data_root = data_root
        self.ignore_dirs = set(ignore_dirs)
        self.index_file = index_file
        self.supported_extensions = supported_extensions
        self.root_index = os.path.abspath(os.path.join(self.data_root,index_file))

    def walk_and_sort(self):
        """Returns a list of data sources, ordered by desired save order."""

        ordered_branches = []
        ordered_sources = []
        sources = []
        for root, dirs, files in os.walk(self.data_root):
            # only consider roots that have an index file
            if self.index_file in files:
                root_index = os.path.join(root, self.index_file)

                with open(root_index) as f:
                    index = dict(json.load(f))
                    # get the ordering for this scope
                    index = index['ordering']

                    # TODO: handle case of directory and file in same scope with the same name
                    # TODO: handle case of two files with the same name and different supported extensions
                    # TODO: Check the implementation for multiple nested directories and rewrite accordingly
                    for entry in index:
                        entry_path = os.path.join(root, entry)

                        # build an ordered list of data branches from the indexed directories
                        if os.path.exists(entry_path) and entry_path not in ordered_branches:
                            ordered_branches.append(entry_path)

                        # build a list of data sources, from the indexed files
                        for ext in self.supported_extensions:
                            source_path = entry_path + ext
                            if os.path.exists(source_path):
                                sources.append(source_path)
        ordered_sources.extend(ordered_branches)

        # each ordered branch will be replaced with an ordered list of the files it contains
        for index, branch in enumerate(ordered_sources):
            ordered_sources[index] = [source for source in sources if source.startswith(branch)]

        # return a flattened list that is ordered for loading to the data store
        return list(chain.from_iterable(ordered_sources))

    def freight(self):
        """Takes the ordered list of data sources, and builds a new list of tuples, with (model, data_source).

        Getting the Model relies on a naming convention in the dataset:
        * the data_source filename is the destination Model name, in lowercase, and the Model itself should be in title case.
        * the data_source parent directory is the module that holds the Model

        """

        freight = []

        for data_source in self.walk_and_sort():
            full_path, ext = os.path.splitext(data_source)
            head, model_name = os.path.split(full_path)
            head, module_name = os.path.split(head)
            model = get_model(module_name, model_name.title())
            freight.append((model, data_source))

        return freight