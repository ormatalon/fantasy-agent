"""dsprep - reusable data processing and feature engineering for tabular ML.

Model-agnostic: every processor is fitted on train only, takes a per-column spec, and can produce
several output styles (CatBoost-native, GBM-encoded, linear-ready) from the same data.

Modules
  decisions   load eda_decisions.yml, apply overrides, re-run cleaning, suggest specs
  split       make_split / split_report / separate_unlabeled (none, random, stratified, group, time);
              make_cv_folds is for the modeling step
  target      TargetProcessor (binary, multiclass, multilabel, regression, count, time_to_event)
  numeric     NumericProcessor, add_ratio_features
  categorical CategoricalProcessor (native, onehot, ordinal, target, count, frequency, hashing)
  text        TextProcessor (stats, tfidf[+svd], keywords, embedding, native)
  lists       ListProcessor (stats, multihot, count, numeric, native), parse_list
  datetime_features  DatetimeProcessor (parts, cyclical, elapsed, diffs, holidays)
  aggregates  add_history_features (past-only), GroupStatsProcessor
  features    FeatureProcessor (runs processors together, log, feature dictionary)
  selection   constant / duplicate / correlated features, leakage scan, null importance
  validate    check_frames, drift_report (PSI), baseline_check
  report      save_frames, save_object, write_processing_record
"""
from .aggregates import GroupStatsProcessor, add_history_features
from .categorical import CategoricalProcessor
from .datetime_features import DatetimeProcessor
from .decisions import (apply_cleaning, apply_overrides, decisions_summary, load_decisions, open_questions,
                        suggest_specs, to_python_literal)
from .features import FeatureProcessor
from .lists import ListProcessor, parse_list
from .numeric import NumericProcessor, add_ratio_features
from .report import save_frames, save_object, write_processing_record
from .selection import (constant_features, correlated_pairs, duplicate_features, leakage_scan, null_importance,
                        screening_report)
from .split import make_cv_folds, make_split, separate_unlabeled, split_report
from .target import TargetProcessor
from .text import TextProcessor
from .validate import baseline_check, check_frames, drift_report, psi

__version__ = "0.2.0"
