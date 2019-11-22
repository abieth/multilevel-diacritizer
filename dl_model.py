import os
import re
import zipfile

from tensor2tensor.data_generators.problem import DatasetSplit
from tensor2tensor.data_generators.text_problems import Text2TextProblem, VocabType
from tensor2tensor.data_generators.generator_utils import maybe_download
from tensor2tensor.utils import registry

from processing import convert_to_pattern, convert_non_arabic, clear_diacritics


@registry.register_problem
class PatternsDiacritization(Text2TextProblem):

    def __init__(self, *args, **kwargs):
        super(PatternsDiacritization, self).__init__(*args, **kwargs)
        self.download_url = None  # It will be changed before calling generate_data function.

    def generate_samples(self, data_dir, tmp_dir, dataset_split):
        dataset_path = maybe_download(tmp_dir, os.path.basename(self.download_url), self.download_url)
        dataset_zip = zipfile.ZipFile(dataset_path)
        for file_name in dataset_zip.namelist():
            file_path = os.path.join(tmp_dir, file_name)
            if not os.path.exists(file_path):
                dataset_zip.extract(file_name, tmp_dir)
        dataset_split = 'dev' if dataset_split == DatasetSplit.EVAL else dataset_split
        with open(os.path.join(tmp_dir, self.dataset_filename()+'_'+dataset_split+'.txt'), encoding='utf-8')\
                as dataset_file:
            for sentence in dataset_file:
                diacritized_patterns_sentence = convert_non_arabic(convert_to_pattern(sentence.strip('\n')))
                undiacritized_patterns_sentence = clear_diacritics(diacritized_patterns_sentence)
                yield {
                    'inputs': undiacritized_patterns_sentence,
                    'targets': diacritized_patterns_sentence
                }

    def get_or_create_vocab(self, data_dir, tmp_dir, force_get=False):
        vocab_file_path = os.path.join(data_dir, self.vocab_filename)
        if not os.path.exists(vocab_file_path):
            vocab = {self.oov_token}
            for s in self.generate_text_for_vocab(data_dir, tmp_dir):
                vocab.update(s.split(' '))
            with open(os.path.join(data_dir, self.vocab_filename), 'w', encoding='utf-8') as vocab_file:
                for p in vocab:
                    print(p, file=vocab_file)
        return super(PatternsDiacritization, self).get_or_create_vocab(data_dir, tmp_dir, force_get)

    @property
    def vocab_type(self):
        return VocabType.TOKEN

    @property
    def oov_token(self):
        return '<UNK>'

    def dataset_filename(self):
        return 'ATB3'

    @property
    def multiprocess_generate(self):
        return False

    def prepare_to_generate(self, data_dir, tmp_dir):
        pass

    @property
    def num_generate_tasks(self):
        return os.cpu_count()

    @property
    def num_training_examples(self):
        return None

    @property
    def is_generate_per_split(self):
        return True


PROBLEM_NAME = '_'.join(map(str.lower, re.findall('[A-Z][a-z]*', PatternsDiacritization.__name__)))


def generate_data(tmp_dir, data_dir, download_url):
    assert isinstance(tmp_dir, str)
    assert isinstance(data_dir, str)
    problem = registry.problem(PROBLEM_NAME)
    assert isinstance(problem, PatternsDiacritization)
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    problem.download_url = download_url
    problem.generate_data(data_dir, tmp_dir)
