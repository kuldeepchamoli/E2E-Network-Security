import pandas as pd
from sklearn.model_selection import train_test_split
from src.NetworkSecurity.logging.logger import logger
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import GradientBoostingClassifier, AdaBoostClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.svm import SVC
# from xgboost import XGBClassifier
import mlflow
from sklearn.preprocessing import StandardScaler
import pickle
from urllib.parse import urlparse
from mlflow.models import infer_signature
from sklearn.model_selection import GridSearchCV
from dotenv import load_dotenv
import os
from src.NetworkSecurity.entity.config_entity import ModelTrainerConfig

load_dotenv()

class ModelTrainer:
    def __init__(self,config: ModelTrainerConfig):
        self.config = config
        os.environ["MLFLOW_TRACKING_USERNAME"] = os.getenv("MLFLOW_TRACKING_USERNAME")
        os.environ["MLFLOW_TRACKING_PASSWORD"] = os.getenv("MLFLOW_TRACKING_PASSWORD")
    
    def train(self):

        train_data = pd.read_csv(self.config.train_data_path)
        test_data = pd.read_csv(self.config.test_data_path)

        # Split features & target
        x_train = train_data.drop(columns=[self.config.target_column], axis=1)
        y_train = train_data[self.config.target_column]
        x_test = test_data.drop(columns=[self.config.target_column], axis=1)
        y_test = test_data[self.config.target_column]

        model_mapping = {
            "LogisticRegression": LogisticRegression(),
            "DecisionTreeClassifier": DecisionTreeClassifier(),
            "GradientBoostingClassifier": GradientBoostingClassifier(),
            "AdaBoostClassifier": AdaBoostClassifier(),
            "KNeighborsClassifier": KNeighborsClassifier(),
            "SVC": SVC()
        }

        # Standardize features
        ss = StandardScaler()
        x_train = ss.fit_transform(x_train)

        # Standardize test features
        x_test = ss.transform(x_test)

        scaler_path = os.path.join(self.config.root_dir, self.config.standard_scaler_name)
        with open(scaler_path, "wb") as f:
            pickle.dump(ss, f)            

        mlflow.set_tracking_uri(str(self.config.mlflow_uri))  # For tracking runs
        mlflow.set_registry_uri(str(self.config.mlflow_uri))  # For model registry
        mlflow.set_experiment(self.config.mlflow_experiment)  # Set the experiment

        tracking_url_type_store = urlparse(mlflow.get_tracking_uri()).scheme
        signature = infer_signature(x_train, y_train)

        models = self.config.models
        hyperparams = self.config.hyperparams

        best_model = None
        best_score = -float("inf")
        best_run_id = None

        for model_name in models:
            model = model_mapping[model_name]
            params = hyperparams.get(model_name, {})

            grid_search = GridSearchCV(model, param_grid=params, cv=5, scoring="accuracy", n_jobs=-1)
            grid_search.fit(x_train, y_train)

            for param, score in zip(grid_search.cv_results_["params"], grid_search.cv_results_["mean_test_score"]):
                with mlflow.start_run() as run:
                    print(f"{param}: {score:.4f}")

                    mlflow.log_params(param)  
                    mlflow.log_metric("train_accuracy", f"{score:.4f}")
                    mlflow.log_param("model_name", model_name)

                    # Track best model based on training accuracy (since GridSearchCV only works on train)
                    if score > best_score:  
                        best_score = score  
                        best_model = grid_search.best_estimator_  
                        best_param = param 
                        best_model_name = model_name

        #  Now test the best model on the test set
        test_acc = best_model.score(x_test, y_test)
        
        model_filename = f"{self.config.root_dir}/{best_model_name}.pkl"
        # Save the best model
        with open(model_filename, "wb") as f:
            pickle.dump(best_model, f)

        # Save metadata (best parameters)
        metadata_filename = f"{self.config.root_dir}/{best_model_name}_metadata.txt"
        with open(metadata_filename, "w") as f:
            f.write(f"Model: {best_model_name}\n")
            f.write(f"Best Params: {best_param}\n")
            f.write(f"Train Accuracy: {best_score}\n")

        # Log the best model based on test accuracy
        with mlflow.start_run():
            mlflow.log_params(best_param)
            mlflow.log_metric("test_accuracy", f"{test_acc:.4f}")  # Now test accuracy is logged
            mlflow.log_param("model_name",best_model_name)

            if tracking_url_type_store != "file":
                mlflow.sklearn.log_model(best_model, "network_model", registered_model_name="Best Model", signature=signature)
            else:
                mlflow.sklearn.log_model(best_model, "network_model", signature=signature)
            
            experiment = mlflow.get_experiment_by_name(self.config.mlflow_experiment)
            experiment_id = experiment.experiment_id
            df = mlflow.search_runs(experiment_ids=[experiment_id])
            latest_run_id = df["run_id"][0]
        
        return latest_run_id