void id76(int array[], int len){
  int i = 0;
  while (i < len) {
    array[i] = 42;
    if (i > 10)
      i = 0;
    i++;
  }
}