import yaml

class AppConfig:
    def __init__(self, config_file_path):
        with open(config_file_path, 'r') as config_file:
            config_hash = yaml.load(config_file, Loader=yaml.FullLoader)
        
        # Set the configuration variables as attributes of the AppConfig instance
        for name, value in config_hash.items():
            setattr(self, name, value)