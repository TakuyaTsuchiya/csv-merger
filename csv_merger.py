#!/usr/bin/env python3
"""
CSV結合ツール - 交渉履歴CSVに会員番号を付加する

使用方法:
    python csv_merger.py [options]
    
オプション:
    --negotiate: 交渉履歴CSVファイルパス (デフォルト: TBL_NEGOTIATE.csv)
    --contract: 案件情報CSVファイルパス (デフォルト: プラザ全案件ContractList_20250801151720.csv)
    --output: 出力ファイルパス (デフォルト: merged_output.csv)
    --chunk-size: チャンクサイズ (デフォルト: 100000)
"""

import argparse
import os
import sys
import time
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple
import warnings

import pandas as pd
import chardet
from tqdm import tqdm
import psutil

# 警告を無視
warnings.filterwarnings('ignore')


class CSVMerger:
    """CSV結合処理のメインクラス"""
    
    def __init__(self, negotiate_file: str, contract_file: str, output_file: str, chunk_size: int = 100000):
        # スクリプトのディレクトリを基準にパスを構築
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        
        # ファイルパスを絶対パスに変換（csv_mergerディレクトリ内を想定）
        self.negotiate_file = os.path.join(self.script_dir, negotiate_file) if not os.path.isabs(negotiate_file) else negotiate_file
        self.contract_file = os.path.join(self.script_dir, contract_file) if not os.path.isabs(contract_file) else contract_file
        self.output_file = os.path.join(self.script_dir, output_file) if not os.path.isabs(output_file) else output_file
        self.chunk_size = chunk_size
        
        # ログとディレクトリの設定（csv_mergerディレクトリ内）
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_dir = os.path.join(self.script_dir, 'logs')
        self.output_dir = os.path.join(self.script_dir, 'output')
        
        # 統計情報
        self.stats = {
            'total_rows': 0,
            'matched_rows': 0,
            'unmatched_rows': 0,
            'processing_time': 0,
            'memory_usage': 0
        }
        
        self._ensure_directories()
        self._setup_logger()
    
    def _ensure_directories(self):
        """必要なディレクトリを作成"""
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
    
    def _setup_logger(self):
        """ロガーの設定"""
        log_file = os.path.join(self.log_dir, f'process_{self.timestamp}.log')
        
        # ロガーの設定
        self.logger = logging.getLogger('CSVMerger')
        self.logger.setLevel(logging.INFO)
        
        # ファイルハンドラー
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setLevel(logging.INFO)
        
        # コンソールハンドラー
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        
        # フォーマッター
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        
        self.logger.addHandler(fh)
        self.logger.addHandler(ch)
        
        self.logger.info(f"CSV結合処理を開始します。ログファイル: {log_file}")
    
    def detect_encoding(self, file_path: str) -> str:
        """ファイルのエンコーディングを自動検出"""
        self.logger.info(f"エンコーディング検出中: {file_path}")
        
        # 日本語エンコーディングの優先順位
        japanese_encodings = ['utf-8', 'shift_jis', 'cp932', 'euc-jp', 'iso-2022-jp']
        
        # chardetで検出を試みる
        try:
            with open(file_path, 'rb') as f:
                # ファイルの一部を読み込んで検出
                raw_data = f.read(100000)  # 100KB
                result = chardet.detect(raw_data)
                
                if result['encoding']:
                    detected = result['encoding'].lower()
                    confidence = result['confidence']
                    self.logger.info(f"検出されたエンコーディング: {detected} (信頼度: {confidence:.2f})")
                    
                    # 信頼度が低い場合は日本語エンコーディングを試す
                    if confidence < 0.7:
                        for enc in japanese_encodings:
                            try:
                                with open(file_path, 'r', encoding=enc) as test_f:
                                    test_f.read(1000)
                                self.logger.info(f"エンコーディング確定: {enc}")
                                return enc
                            except:
                                continue
                    
                    return detected
        except Exception as e:
            self.logger.warning(f"chardet検出エラー: {e}")
        
        # フォールバック: 各エンコーディングを試す
        for encoding in japanese_encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    f.read(1000)
                self.logger.info(f"エンコーディング確定: {encoding}")
                return encoding
            except:
                continue
        
        # デフォルト
        self.logger.warning("エンコーディング検出失敗。UTF-8を使用します。")
        return 'utf-8'
    
    def read_contract_data(self) -> Dict[str, str]:
        """案件情報CSVを読み込み、管理番号をキーとした辞書を作成"""
        self.logger.info(f"案件情報CSV読み込み開始: {self.contract_file}")
        
        if not os.path.exists(self.contract_file):
            raise FileNotFoundError(f"案件情報ファイルが見つかりません: {self.contract_file}")
        
        encoding = self.detect_encoding(self.contract_file)
        
        try:
            # 案件情報CSVを読み込み
            df = pd.read_csv(self.contract_file, encoding=encoding, dtype=str)
            self.logger.info(f"案件情報CSV読み込み完了: {len(df)}件")
            
            # A列（管理番号）とB列（会員番号）の列名を取得
            if len(df.columns) < 2:
                raise ValueError("案件情報CSVには最低2列必要です")
            
            manage_no_col = df.columns[0]  # A列
            member_no_col = df.columns[1]   # B列
            
            # 管理番号をキー、会員番号を値とする辞書を作成
            contract_dict = {}
            for _, row in df.iterrows():
                manage_no = str(row[manage_no_col]).strip()
                member_no = str(row[member_no_col]).strip()
                if manage_no and manage_no != 'nan':
                    contract_dict[manage_no] = member_no
            
            self.logger.info(f"辞書作成完了: {len(contract_dict)}件")
            return contract_dict
            
        except Exception as e:
            self.logger.error(f"案件情報CSV読み込みエラー: {e}")
            raise
    
    def process_negotiate_chunks(self, contract_dict: Dict[str, str]):
        """交渉履歴CSVをチャンク単位で処理し、会員番号を付加"""
        self.logger.info(f"交渉履歴CSV処理開始: {self.negotiate_file}")
        
        if not os.path.exists(self.negotiate_file):
            raise FileNotFoundError(f"交渉履歴ファイルが見つかりません: {self.negotiate_file}")
        
        encoding = self.detect_encoding(self.negotiate_file)
        
        # ファイルサイズから総行数を推定
        file_size = os.path.getsize(self.negotiate_file)
        estimated_rows = file_size // 200  # 1行あたり約200バイトと仮定
        
        # 最初の行を読んでヘッダーの有無を確認
        first_chunk = pd.read_csv(self.negotiate_file, encoding=encoding, nrows=1, dtype=str, header=None)
        
        # カラム数を確認
        num_columns = len(first_chunk.columns)
        if num_columns < 2:
            raise ValueError("交渉履歴CSVには最低2列必要です")
        
        # 会員番号列を挿入する位置を決定（Column2の右隣 = インデックス2）
        insert_position = 2
        
        # 出力用の列構成を作成（ヘッダーなし）
        output_columns = list(range(insert_position)) + ['member_no'] + list(range(insert_position, num_columns))
        
        # チャンク処理
        start_time = time.time()
        first_chunk_written = False
        
        with tqdm(total=estimated_rows, desc="処理中", unit="行") as pbar:
            for chunk_num, chunk in enumerate(pd.read_csv(
                self.negotiate_file, 
                encoding=encoding, 
                chunksize=self.chunk_size,
                dtype=str,
                header=None  # ヘッダーなしとして読み込み
            )):
                self.logger.info(f"チャンク {chunk_num + 1} 処理中 ({len(chunk)}行)")
                
                # 管理番号（Column2 = インデックス1）で会員番号を検索
                chunk['member_no'] = chunk[1].apply(
                    lambda x: contract_dict.get(str(x).strip(), '') if pd.notna(x) else ''
                )
                
                # マッチング統計
                matched = chunk['member_no'].notna() & (chunk['member_no'] != '')
                self.stats['matched_rows'] += matched.sum()
                self.stats['unmatched_rows'] += (~matched).sum()
                self.stats['total_rows'] += len(chunk)
                
                # 列の順序を調整（数値インデックスを使用）
                chunk = chunk[[0, 1, 'member_no'] + list(range(2, num_columns))]
                
                # 出力ファイルに追記
                mode = 'w' if not first_chunk_written else 'a'
                header = False  # ヘッダーなしで出力
                
                chunk.to_csv(
                    self.output_file,
                    mode=mode,
                    header=header,
                    index=False,
                    encoding=encoding
                )
                
                first_chunk_written = True
                pbar.update(len(chunk))
                
                # メモリ使用量を記録
                process = psutil.Process()
                self.stats['memory_usage'] = max(
                    self.stats['memory_usage'],
                    process.memory_info().rss / 1024 / 1024  # MB
                )
        
        self.stats['processing_time'] = time.time() - start_time
        self.logger.info("交渉履歴CSV処理完了")
    
    def output_statistics(self):
        """処理統計の出力"""
        stats_file = os.path.join(self.output_dir, f'stats_{self.timestamp}.txt')
        
        stats_text = f"""
========================================
CSV結合処理統計
========================================
処理日時: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}

入力ファイル:
  - 交渉履歴: {self.negotiate_file}
  - 案件情報: {self.contract_file}

出力ファイル: {self.output_file}

処理結果:
  - 総処理件数: {self.stats['total_rows']:,}件
  - マッチング成功: {self.stats['matched_rows']:,}件 ({self.stats['matched_rows']/max(self.stats['total_rows'], 1)*100:.1f}%)
  - マッチング失敗: {self.stats['unmatched_rows']:,}件 ({self.stats['unmatched_rows']/max(self.stats['total_rows'], 1)*100:.1f}%)

パフォーマンス:
  - 処理時間: {self.stats['processing_time']:.1f}秒
  - 処理速度: {self.stats['total_rows']/max(self.stats['processing_time'], 1):.0f}件/秒
  - 最大メモリ使用量: {self.stats['memory_usage']:.1f}MB
========================================
"""
        
        # ファイル出力
        with open(stats_file, 'w', encoding='utf-8') as f:
            f.write(stats_text)
        
        # コンソール出力
        print(stats_text)
        self.logger.info(f"統計情報を保存しました: {stats_file}")
    
    def run(self):
        """メイン処理の実行"""
        try:
            self.logger.info("=" * 50)
            self.logger.info("CSV結合処理開始")
            self.logger.info("=" * 50)
            
            # 案件情報を読み込み
            contract_dict = self.read_contract_data()
            
            # 交渉履歴を処理
            self.process_negotiate_chunks(contract_dict)
            
            # 統計情報を出力
            self.output_statistics()
            
            self.logger.info("=" * 50)
            self.logger.info("CSV結合処理完了")
            self.logger.info("=" * 50)
            
        except Exception as e:
            self.logger.error(f"処理中にエラーが発生しました: {e}")
            raise


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description='交渉履歴CSVに会員番号を付加する',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--negotiate',
        default='TBL_NEGOTIATE.csv',
        help='交渉履歴CSVファイルパス (デフォルト: TBL_NEGOTIATE.csv)'
    )
    
    parser.add_argument(
        '--contract',
        default='プラザ全案件ContractList_20250801151720.csv',
        help='案件情報CSVファイルパス (デフォルト: プラザ全案件ContractList_20250801151720.csv)'
    )
    
    parser.add_argument(
        '--output',
        default='merged_output.csv',
        help='出力ファイルパス (デフォルト: merged_output.csv)'
    )
    
    parser.add_argument(
        '--chunk-size',
        type=int,
        default=100000,
        help='チャンクサイズ (デフォルト: 100000)'
    )
    
    args = parser.parse_args()
    
    # CSVMergerインスタンスを作成して実行
    merger = CSVMerger(
        negotiate_file=args.negotiate,
        contract_file=args.contract,
        output_file=args.output,
        chunk_size=args.chunk_size
    )
    
    try:
        merger.run()
        print("\n処理が正常に完了しました。")
        sys.exit(0)
    except Exception as e:
        print(f"\nエラー: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()