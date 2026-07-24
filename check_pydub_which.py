import inspect
import pydub.utils as utils

print('utils module:', utils.__file__)
print('utils.which source:')
print(inspect.getsource(utils.which))
