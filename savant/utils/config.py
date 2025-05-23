import os

from savant.utils.log import get_logger

logger = get_logger(__name__)


def req_config(name):
    """Get a configuration value from the environment.

    :param name: The name of the environment variable.
    """
    val = os.environ.get(name)
    if val is None:
        logger.error(
            'Mandatory environment configuration variable "%s" not found.', name
        )
        logger.error('Configured environment variables:')
        for k, v in os.environ.items():
            logger.error('\t%s=%s', k, v)
        raise ValueError(
            f'Configuration Value Not Found in environment variables: {name}'
        )
    logger.info('Applying Configuration Value: %s=%s', name, val)
    return val


def opt_config(name, default=None, convert=None):
    """Get an optional configuration value from the environment.

    :param name: The name of the environment variable.
    :param default: The default value to return if the configuration variable is not set.
    :param convert: A function to convert the value from string to a different type.
    """

    conf_str = os.environ.get(name)
    val = default
    if conf_str:
        val = convert(conf_str) if convert else conf_str
    logger.info('Applying Configuration Value: %s=%s', name, val)
    return val


def strtobool(val):
    """Convert a string representation of truth to true (1) or false (0).

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.

    Copy of distutils.util.strtobool because of
    `The distutils package is deprecated and slated for removal in Python 3.12.`
    """
    val = val.lower()
    if val in ('y', 'yes', 't', 'true', 'on', '1'):
        return 1
    elif val in ('n', 'no', 'f', 'false', 'off', '0'):
        return 0
    else:
        raise ValueError('invalid truth value %r' % (val,))
